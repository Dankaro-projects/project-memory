"""The JSON reports agent hosts return: their schemas, their budgets and their validation.

An agent check returns a review report and a delegated worker returns a work
report. Both are bounded JSON documents with exact fields, so the field rules,
the lesson proposals and the serialized length limit are defined once here and
used by reviews.py and delegation.py.
"""
import json

from .core import InvalidRecord, dumps, _text

REPORT_MAX_CHARACTERS = 16000
REPORT_TARGET_CHARACTERS = 12000
WORK_REPORT_MAX_CHARACTERS = 32_000

REPORT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'verdict': {'type': 'string', 'enum': ['pass', 'changes_required', 'uncertain']},
        'summary': {'type': 'string'},
        'checks': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {k: {'type': 'string'} for k in ('criterion', 'evidence', 'result')},
            'required': ['criterion', 'evidence', 'result']}},
        'findings': {'type': 'array', 'items': {'type': 'string'}},
        'lesson_proposals': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {k: {'type': 'string'} for k in ('proposal', 'basis', 'conditions', 'exceptions')},
            'required': ['proposal', 'basis', 'conditions', 'exceptions']}}
    }, 'required': ['verdict', 'summary', 'checks', 'findings', 'lesson_proposals']}

WORK_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'summary': {'type': 'string'},
        'result': {'type': 'string', 'enum': ['complete', 'partial', 'blocked']},
        'changed_files': {'type': 'array', 'items': {'type': 'string'}},
        'checks_run': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
            'properties': {'command': {'type': 'string'}, 'outcome': {'type': 'string'}},
            'required': ['command', 'outcome']}},
        'notes': {'type': 'string'},
        'lesson_proposals': REPORT_SCHEMA['properties']['lesson_proposals'],
    },
    'required': ['summary', 'result', 'changed_files', 'checks_run', 'notes', 'lesson_proposals']}


def entries(items, fields, *, limit, list_message, item_message, minimum=0):
    """Check a bounded list of objects that each hold exactly these fields as bounded text.

    The criterion checks, the constraint checks, the checks a worker ran and the
    lesson proposals all have this shape, so both reports share this rule.
    """
    if not isinstance(items, list) or not minimum <= len(items) <= limit:
        raise InvalidRecord(list_message)
    for item in items:
        if not isinstance(item, dict) or set(item) != set(fields):
            raise InvalidRecord(item_message)
        for key, value in item.items():
            _text(value, key, 6000)
    return items


def validate_lesson_proposals(items):
    """Check the lesson proposals that review reports and work reports share."""
    return entries(items, ('proposal', 'basis', 'conditions', 'exceptions'), limit=30,
                   list_message='The review contains too many findings or proposals.',
                   item_message='A lesson proposal needs its basis, conditions and exceptions.')


def report_schema(snapshot):
    """The review report schema for one snapshot, with the identifiers and the text budget of that check."""
    schema = json.loads(dumps(REPORT_SCHEMA))
    text_fields = len(snapshot.get('checklist', [])) + 2 * len(snapshot.get('constraints', []))
    evidence_limit = min(600, 8000 // max(1, text_fields))
    schema['description'] = (
        f'The complete serialized JSON report must not exceed {REPORT_MAX_CHARACTERS:,} characters, '
        f'including field names, punctuation and escaped text. Aim for at most {REPORT_TARGET_CHARACTERS:,} characters. '
        'Budget space across every required check before drafting. Use short file/line or record references; '
        'do not repeat requirement text or the same explanation in several fields. '
        f'For this review, draft each evidence string and constraint reason within {evidence_limit * 2 // 3} characters; '
        f'the schema allows at most {evidence_limit}. Write complete short sentences; never cut a sentence or word to fit. '
        'Keep findings and lesson_proposals together within 2,000 characters. '
        'Preserve all required IDs, distinct findings, conditions, exceptions and uncertainty; shorten wording, not coverage. '
        'Return compact JSON and check its total length before submitting.')
    schema['properties']['summary']['maxLength'] = 600
    checks = schema['properties']['checks']
    checks['items']['properties']['evidence']['maxLength'] = evidence_limit
    checks['items']['properties']['result']['enum'] = ['met','unmet','unknown']
    if snapshot.get('checklist'):
        checks['items']['properties']['criterion']['enum'] = [item['id'] for item in snapshot['checklist']]
        checks.update(minItems=len(snapshot['checklist']),maxItems=len(snapshot['checklist']))
    if 'constraints' in snapshot:
        schema['properties']['constraint_checks'] = {
            'type':'array', 'items':{'type':'object', 'additionalProperties':False,
                'properties':{k:{'type':'string'} for k in ('constraint','applicability','reason','evidence','result')},
                'required':['constraint','applicability','reason','evidence','result']}}
        schema['required'].append('constraint_checks')
        mapping = schema['properties']['constraint_checks']
        for field in ('reason', 'evidence'):
            mapping['items']['properties'][field]['maxLength'] = evidence_limit
        mapping['items']['properties']['constraint']['enum'] = [item['id'] for item in snapshot['constraints']]
        mapping['items']['properties']['applicability']['enum'] = ['applies','not_applicable','uncertain']
        mapping['items']['properties']['result']['enum'] = ['met','unmet','unknown','not_applicable']
        mapping.update(minItems=len(snapshot['constraints']),maxItems=len(snapshot['constraints']))
    return schema


def validate_report(report, checklist=None, constraints=None):
    """Check a review report: its verdict, every checklist identifier, every constraint and its length."""
    required_fields = set(REPORT_SCHEMA['required']) | ({'constraint_checks'} if constraints is not None else set())
    if not isinstance(report, dict) or set(report) != required_fields:
        raise InvalidRecord('The reviewer did not return the required report fields.')
    if report['verdict'] not in {'pass', 'changes_required', 'uncertain'}:
        raise InvalidRecord('The reviewer returned an invalid verdict.')
    _text(report['summary'], 'review summary', 6000)
    entries(report['checks'], ('criterion','evidence','result'), limit=100, minimum=1,
            list_message='The reviewer must provide explicit criterion checks.',
            item_message='Each check needs a criterion, evidence and result.')
    for item in report['checks']:
        if item['result'] not in {'met','unmet','unknown'}:
            raise InvalidRecord('A check result must be met, unmet or unknown.')
    if report['verdict']=='pass' and any(x['result']!='met' for x in report['checks']):
        raise InvalidRecord('A passing report cannot contain unmet or unknown checks.')
    if checklist is not None:
        expected = {item['id'] for item in checklist}
        observed = [item['criterion'] for item in report['checks']]
        if set(observed) != expected or len(observed) != len(expected):
            raise InvalidRecord('The reviewer must assess every checklist ID exactly once; missing proof must be unknown.')
    if constraints is not None:
        mapping = entries(report['constraint_checks'], ('constraint','applicability','reason','evidence','result'),
                          limit=len(constraints), minimum=len(constraints),
                          list_message='Explain the applicability of every project constraint exactly once.',
                          item_message='Each constraint needs applicability, reason, evidence and result.')
        ids = []
        mandatory = {item['id'] for item in constraints if item.get('always_applies')}
        for item in mapping:
            ids.append(item['constraint'])
            if item['applicability'] not in {'applies','not_applicable','uncertain'}:
                raise InvalidRecord('Constraint applicability must be applies, not_applicable or uncertain.')
            if item['constraint'] in mandatory and item['applicability']=='not_applicable':
                raise InvalidRecord('The recorded scope always applies to this task.')
            allowed = {'not_applicable'} if item['applicability']=='not_applicable' else {'unknown'} if item['applicability']=='uncertain' else {'met','unmet','unknown'}
            if item['result'] not in allowed:
                raise InvalidRecord('The constraint result must agree with its applicability.')
            if report['verdict']=='pass' and item['result'] not in {'met','not_applicable'}:
                raise InvalidRecord('A passing report cannot leave an applicable or uncertain constraint unresolved.')
        if set(ids)!={item['id'] for item in constraints} or len(set(ids))!=len(ids):
            raise InvalidRecord('Explain the applicability of every project constraint exactly once.')
    if not isinstance(report['findings'], list) or len(report['findings'])>30:
        raise InvalidRecord('The review contains too many findings or proposals.')
    for value in report['findings']:
        _text(value, 'finding', 6000)
    validate_lesson_proposals(report['lesson_proposals'])
    if len(dumps(report))>REPORT_MAX_CHARACTERS:
        raise InvalidRecord(f'The review report exceeds {REPORT_MAX_CHARACTERS:,} characters.')
    return report


def work_schema():
    """The work report schema with its budget and its reporting rules."""
    schema = json.loads(dumps(WORK_SCHEMA))
    schema['description'] = (
        f'The complete serialized JSON report must not exceed {WORK_REPORT_MAX_CHARACTERS:,} characters. '
        'List every file you changed in changed_files. Report each check you ran with its command or verification step '
        'and its actual outcome; a verification step can be a comparison with a source document or a calculation that you repeated. '
        'Use result partial or blocked when the criterion is not fully met, and explain why in notes. '
        'Write complete short sentences. Keep lesson_proposals within 2,000 characters.')
    schema['properties']['summary']['maxLength'] = 2000
    schema['properties']['notes']['maxLength'] = 4000
    return schema


def validate_work_report(report):
    """Check a worker report: its result, the files it changed, the checks it ran and its length."""
    if not isinstance(report, dict) or set(report) != set(WORK_SCHEMA['required']):
        raise InvalidRecord('The worker did not return the required report fields.')
    _text(report['summary'], 'work summary', 6000)
    if report['result'] not in {'complete', 'partial', 'blocked'}:
        raise InvalidRecord('The work result must be complete, partial or blocked.')
    if not isinstance(report['changed_files'], list) or len(report['changed_files']) > 1000:
        raise InvalidRecord('changed_files must be a list of at most 1,000 paths.')
    for name in report['changed_files']:
        _text(name, 'changed file', 1000)
    entries(report['checks_run'], ('command', 'outcome'), limit=100,
            list_message='checks_run must be a list of at most 100 checks.',
            item_message='Each check needs its command and outcome.')
    if not isinstance(report['notes'], str) or len(report['notes']) > 6000:
        raise InvalidRecord('notes must be text of at most 6,000 characters.')
    validate_lesson_proposals(report['lesson_proposals'])
    if len(dumps(report)) > WORK_REPORT_MAX_CHARACTERS:
        raise InvalidRecord(f'The work report exceeds {WORK_REPORT_MAX_CHARACTERS:,} characters.')
    return report
