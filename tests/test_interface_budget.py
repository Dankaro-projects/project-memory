"""Size budgets of the control panel (sections 5.1 and 11.6 of the build specification).

The budgets keep the browser interface small enough to review. They count code characters,
which excludes every space, tab and line break, because a line budget can be met by packing
more code onto fewer lines. Wave 8 showed exactly that: lines fell by 14.6 percent while the
code itself fell by 3.9 percent. The budgets are set just above the measured size of
16 September 2026, so they stop growth; a real reduction is still owed and lowers them.
They are measured on the files that viewer.py joins into the page, so a new view file
counts as soon as it ships.
"""
from pathlib import Path
import re
import unittest

from memory_module import viewer

ROOT = Path(__file__).resolve().parents[1] / 'memory_module'
UI = ROOT / 'ui'
TOTAL_SCRIPT_CHARACTERS = 196_000
FILE_BUDGETS = {'core.js': 36_000, 'forms.js': 37_000, 'graphs.js': 41_000}
VIEW_CHARACTERS = 82_000
STYLE_CHARACTERS = 27_000
SHELL_CHARACTERS = 6_600


def size(path):
    """The number of code characters: everything except spaces, tabs and line breaks."""
    return len(re.sub(r'\s+', '', path.read_text(encoding='utf-8')))


class InterfaceBudgetTests(unittest.TestCase):
    """The panel stays within its size budgets with every action it offers."""

    def test_the_joined_scripts_stay_within_the_total_budget(self):
        counts = {name: size(UI / name) for name in viewer.UI_SCRIPTS}
        self.assertLessEqual(sum(counts.values()), TOTAL_SCRIPT_CHARACTERS, counts)

    def test_each_budgeted_script_stays_within_its_budget(self):
        for name, budget in FILE_BUDGETS.items():
            with self.subTest(name=name):
                self.assertIn(name, viewer.UI_SCRIPTS)
                self.assertLessEqual(size(UI / name), budget)

    def test_the_view_files_together_stay_within_their_budget(self):
        views = [name for name in viewer.UI_SCRIPTS if name.startswith('views')]
        self.assertGreaterEqual(len(views), 2, views)
        counts = {name: size(UI / name) for name in views}
        self.assertLessEqual(sum(counts.values()), VIEW_CHARACTERS, counts)

    def test_the_stylesheet_and_the_page_shell_stay_within_their_budgets(self):
        self.assertLessEqual(sum(size(UI / name) for name in viewer.UI_STYLES), STYLE_CHARACTERS)
        self.assertLessEqual(size(ROOT / 'viewer.html'), SHELL_CHARACTERS)

    def test_the_budget_is_measured_with_the_reassess_action_in_place(self):
        # A budget met by dropping an action would hide a lost feature, so the reassess form and its button must ship.
        self.assertIn('P.registerForm("reassess"', (UI / 'forms.js').read_text(encoding='utf-8'))
        self.assertIn('P.formButton("Reassess", "reassess"', (UI / 'views_knowledge.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
