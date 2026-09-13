import os
import tomllib
from pathlib import Path
project=tomllib.loads(Path('pyproject.toml').read_text())['project']
expected='v'+project['version']
if os.environ.get('GITHUB_REF_NAME')!=expected:raise SystemExit('Release must run from tag '+expected)
print('Release version:',project['version'])
