"""Read declared software dependencies and explicitly recorded architecture links."""
import json
from pathlib import Path
import re
import tomllib

from .core import InvalidRecord
from .skills import project_root


def inventory(memory, *, episode_ids=None, source_ids=None):
    from .reviews import project_paths
    from .maps import model
    root, paths = project_paths(project_root(memory))
    manifests = [name for name in paths if Path(name).name in {'package.json', 'pyproject.toml'}
                 or re.fullmatch(r'requirements(?:[._-].+)?\.txt', Path(name).name)]
    if len(manifests) > 100:
        raise InvalidRecord('Dependency inventory exceeds 100 manifests. Select a smaller project root.')
    packages, files, issues = [], [], []
    for name in manifests:
        path = root / name
        count = len(packages)
        try:
            if not path.resolve().is_relative_to(root):
                raise ValueError('Manifest points outside the project.')
            if path.stat().st_size > 1_000_000:
                raise ValueError('Manifest exceeds 1 MB.')
            text = path.read_text(encoding='utf-8')
            entries = declarations(Path(name).name, text)
            for item in entries:
                if 'issue' in item:
                    issues.append({'manifest': name, 'message': item['issue']})
                else:
                    packages.append({**item, 'manifest': name})
        except (OSError, ValueError, TypeError) as exc:
            issues.append({'manifest': name, 'message': str(exc)})
        files.append({'path': name, 'count': len(packages) - count})
    recorded = []
    rows = memory.db.execute("""SELECT s.source_key FROM sources s
        WHERE s.source_key LIKE 'workspace-map:%:architecture'
        AND s.version=(SELECT max(n.version) FROM sources n WHERE n.source_key=s.source_key)""")
    for row in rows:
        episode_id = row['source_key'].split(':')[1]
        if episode_ids is not None and episode_id not in episode_ids:
            continue
        diagram = model(memory, episode_id, 'architecture')
        nodes = {n['id']: n for n in diagram['nodes'] if n['status'] != 'retired'}
        for node in nodes.values():
            uses = [edge for edge in diagram['edges'] if edge['to'] == node['id'] and edge['from'] in nodes
                    and edge['type'] in {'uses', 'depends_on'} and edge['status'] != 'retired']
            if uses:
                recorded.append({'name': node['title'], 'kind': node['kind'], 'description': node['description'],
                    'status': node['status'], 'reference': node['reference'],
                    'reference_status': diagram['reference_status'].get(node['reference']),
                    'source_id': diagram['source_id'], 'episode_id': episode_id,
                    'work_title': memory.episode(episode_id)['title'],
                    'used_by': [{'name': nodes[e['from']]['title'], 'reason': e['reason'], 'status': e['status']} for e in uses]})
    documentation = []
    for source in memory.db.execute("""SELECT s.id,s.title,s.body FROM sources s WHERE s.origin='document'
        AND s.version=(SELECT max(n.version) FROM sources n WHERE n.source_key=s.source_key)
        ORDER BY s.source_key"""):
        if source_ids is not None and source['id'] not in source_ids:
            continue
        for section in dependency_sections(source['title'], source['body']):
            documentation.append({'source_id': source['id'], 'title': source['title'],
                                  'status': memory.source_status(source['id']), **section})
    return {'packages': sorted(packages, key=lambda p: (p['name'].lower(), p['manifest'], p['group'])),
            'documentation': documentation,
            'recorded': recorded, 'manifests': files, 'issues': issues,
            'scope': 'Declared direct dependencies in project package.json, pyproject.toml and requirements*.txt files. Installed, deployed and transitive versions are not assessed.'}


def dependency_sections(title, body):
    """Preserve complete authored sections, including their nested exceptions."""
    lines = body.splitlines(keepends=True)
    headings, fence = [], None
    for i, line in enumerate(lines):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
        if fence or marker:
            continue
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if heading:
            headings.append((i, len(heading[1]), heading[2]))
    sections, covered = [], -1
    for n, (start, level, heading) in enumerate(headings):
        if start < covered or not re.search(r'\bdependenc(?:y|ies)\b', heading, re.I):
            continue
        end = next((i for i, depth, _ in headings[n + 1:] if depth <= level), len(lines))
        sections.append({'heading': heading, 'body': ''.join(lines[start:end])})
        covered = end
    if not sections and re.search(r'\bdependenc(?:y|ies)\b', title, re.I):
        sections.append({'heading': title, 'body': body})
    return sections


def declarations(filename, text):
    result = []

    def requirements(values, group):
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(group + ' must be a list of dependency declarations.')
        for value in values:
            match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$', value.strip())
            if not match or (match[2].strip() and match[2].lstrip()[0] not in '[<=>!~;@('):
                result.append({'issue': 'Unparsed dependency declaration: ' + value})
            else:
                result.append({'name': match[1], 'requirement': match[2].strip() or 'Any version',
                               'group': group, 'ecosystem': 'Python'})

    if filename == 'package.json':
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError('package.json must contain an object.')
        for key, group in [('dependencies', 'Runtime'), ('devDependencies', 'Development'),
                           ('peerDependencies', 'Peer'), ('optionalDependencies', 'Optional')]:
            dependencies = value.get(key, {})
            if not isinstance(dependencies, dict) or not all(isinstance(v, str) for v in dependencies.values()):
                raise ValueError(key + ' must map package names to version declarations.')
            # npm optionalDependencies overrides the same name in dependencies.
            optional = value.get('optionalDependencies', {})
            for name, requirement in dependencies.items():
                if key == 'dependencies' and isinstance(optional, dict) and name in optional:
                    continue
                result.append({'name': name, 'requirement': requirement, 'group': group, 'ecosystem': 'npm'})
    elif filename == 'pyproject.toml':
        value = tomllib.loads(text)
        project = value.get('project', {})
        if not isinstance(project, dict) or not isinstance(value.get('build-system', {}), dict):
            raise ValueError('Project and build-system sections must be tables.')
        requirements(project.get('dependencies', []), 'Runtime')
        optional = project.get('optional-dependencies', {})
        groups = value.get('dependency-groups', {})
        if not isinstance(optional, dict) or not isinstance(groups, dict):
            raise ValueError('Optional dependencies and dependency groups must be tables.')
        for name, deps in optional.items():
            requirements(deps, 'Optional: ' + name)
        requirements(value.get('build-system', {}).get('requires', []), 'Build')
        for name, deps in groups.items():
            if not isinstance(deps, list):
                raise ValueError('Dependency groups must contain lists.')
            requirements([dep for dep in deps if isinstance(dep, str)], 'Group: ' + name)
            for dep in deps:
                if not isinstance(dep, str):
                    result.append({'issue': 'Group ' + name + ' includes ' + json.dumps(dep) + '; see its declaration.'})
        dynamic = project.get('dynamic', [])
        if not isinstance(dynamic, list):
            raise ValueError('Dynamic fields must be a list.')
        if 'dependencies' in dynamic:
            result.append({'issue': 'Runtime dependencies are dynamic and cannot be read from this manifest.'})
    else:
        for line in text.replace('\\\n', '').splitlines():
            value = re.split(r'\s+#', line, maxsplit=1)[0].strip()
            if not value or value.startswith('#'):
                continue
            if value.startswith('-'):
                result.append({'issue': 'Requirements directive needs inspection: ' + value})
            else:
                requirements([value], 'Requirements')
    return result
