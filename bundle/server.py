"""MCPB entry point; the client supplies a directory selected by the user."""
from pathlib import Path
import sys
from memory_module.cli import main
from memory_module.install import setup

project=Path(sys.argv[1]).resolve()
if not (project/'.memory/install.json').exists() and not (project/'.memory/project.sqlite').exists():
    setup(project,client='mcp')
raise SystemExit(main(['serve','--project',str(project)]))
