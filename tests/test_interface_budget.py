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
# Allowance for the Focus section of a work item (section 11.7): at most 8,000 code characters of JavaScript.
# Measured on 17 September 2026: the joined scripts held 194,052 code characters before the section and 201,935
# after it, views_work.js grew by 5,290 and forms.js by 2,593. The allowance is split as 5,300 for the view files
# and 2,700 for forms.js, so the total budget grows by exactly 8,000.
FOCUS_ALLOWANCE = {'views': 5_300, 'forms.js': 2_700}
# Allowance for the Hive view (section 12.10): at most 20,000 code characters of JavaScript. Measured on 17 September 2026:
# the joined scripts held 201,935 code characters before the view and 216,385 after it, a growth of 14,450.
# views_knowledge.js grew by 10,132 with the swarm list and the timeline, and forms.js by 4,318 with the hive_post,
# hive_close and hive_purge forms. The allowance is 10,200 for the view files and 4,400 for forms.js, 14,600 in total.
# The stylesheet budget is unchanged: the two hive rules fit inside it.
HIVE_ALLOWANCE = {'views': 10_200, 'forms.js': 4_400}
HIVE_LIMIT = 20_000
TOTAL_SCRIPT_CHARACTERS = 196_000 + sum(FOCUS_ALLOWANCE.values()) + sum(HIVE_ALLOWANCE.values())
FILE_BUDGETS = {'core.js': 36_000, 'forms.js': 37_000 + FOCUS_ALLOWANCE['forms.js'] + HIVE_ALLOWANCE['forms.js'], 'graphs.js': 41_000}
VIEW_CHARACTERS = 82_000 + FOCUS_ALLOWANCE['views'] + HIVE_ALLOWANCE['views']
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

    def test_the_budget_is_measured_with_the_focus_section_in_place(self):
        # The allowance above pays for these forms and buttons, so the budget may not be met by removing them.
        self.assertLessEqual(sum(FOCUS_ALLOWANCE.values()), 8_000)
        forms = (UI / 'forms.js').read_text(encoding='utf-8')
        views = (UI / 'views_work.js').read_text(encoding='utf-8')
        self.assertIn('P.registerForm("focus_check"', forms)
        self.assertIn('P.registerForm("focus_start"', forms)
        self.assertIn('P.formButton("Set the check", "focus_check"', views)
        self.assertIn('P.formButton("Start the attempts", "focus_start"', views)


    def test_the_budget_is_measured_with_the_hive_view_in_place(self):
        # The hive allowance pays for the view and its three forms, so the budget may not be met by removing them.
        self.assertLessEqual(sum(HIVE_ALLOWANCE.values()), HIVE_LIMIT)
        forms = (UI / 'forms.js').read_text(encoding='utf-8')
        views = (UI / 'views_knowledge.js').read_text(encoding='utf-8')
        for name in ('hive_post', 'hive_close', 'hive_purge'):
            self.assertIn(f'P.registerForm("{name}"', forms)
        self.assertIn('P.registerView("hive"', views)
        self.assertIn('P.formButton("Purge closed swarms", "hive_purge"', views)
        self.assertIn('P.formButton("Close the swarm", "hive_close"', views)


if __name__ == '__main__':
    unittest.main()
