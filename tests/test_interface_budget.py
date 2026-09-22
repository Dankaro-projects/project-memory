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
# Allowance for the Usage view (section 16.5): at most 8,000 code characters of JavaScript. Measured on 17 September 2026:
# the joined scripts held 216,571 code characters before the view and 220,895 after it, a growth of 4,324, all in
# views_knowledge.js with the host cards and the table of routing decisions. The view has no form, so forms.js, core.js
# and the stylesheet are unchanged, and the allowance is 4,400 for the view files.
USAGE_ALLOWANCE = {'views': 4_400}
USAGE_LIMIT = 8_000
# Allowance for the Sessions view (section 17.8): at most 8,000 code characters of JavaScript together with the changed
# Usage view. Measured on 17 September 2026 against main at 88cccdb: views_knowledge.js grew by 2,944 with the Sessions
# view, and forms.js by 1,719 with the session_flag and session_proposal forms. The Usage view then grew by 772 with the
# separated fresh work, cache reads and session statistics. The allowance is 3,800 for the views and 1,750 for forms.js.
SESSIONS_ALLOWANCE = {'views': 3_800, 'forms.js': 1_750}
SESSIONS_LIMIT = 8_000
# Allowance for reconciliation from the panel and the Now and board layout (sections 17.11 and 17.12). Measured on
# 17 September 2026: views_work.js grew by 4,090 with the Needs reconciliation card, the now_list drawer and the paged
# board columns, and forms.js by 1,672 with the reconcile and reconcile_read_only forms. The allowance is 4,100 and 1,700.
PANEL_ACTIONS_ALLOWANCE = {'views': 4_100, 'forms.js': 1_700}
PANEL_ACTIONS_LIMIT = 8_000
# Allowance for the usability review of 21 September 2026: at most 10,000 code characters of JavaScript and 400 of stylesheet.
# The eleven work items of the review cover the reordered work drawer, the Waiting for you card of Now with one button per
# kind, flag and reconciliation decisions without a dialog, quick edits of state and priority, the delegation form that takes
# missing paths, Records filters on change, the narrow screen layout, the accessibility corrections and the snapshot start
# view. Measured on 21 September 2026 against main at ba1fc4d, after the three attention tables of Now were merged into one:
# views_work.js grew by 5,530, views_knowledge.js by 1,946, forms.js by 1,269 and core.js by 1,238, and panel.css grew from
# 26,962 to 27,334. The orchestrator first decided a cap of 8,000 and raised it to 10,000 when the measurement was known;
# the user has not yet confirmed the cap.
USABILITY_ALLOWANCE = {'views': 7_400, 'forms.js': 1_300, 'core.js': 1_300}
USABILITY_LIMIT = 10_000
USABILITY_STYLE_ALLOWANCE = 400
# Temporary allowance for the fixed frame redesign. The user decided on 21 September 2026: at most 20,000 code characters of
# JavaScript and 8,000 of stylesheet while the old cards and drawers exist beside the new frame and panes. The allowance
# grows with each work item of the redesign by its measured size and stays under these caps. When the last item closes, the
# user sets new base budgets from the measured size, and this allowance and the earlier ones, including the usability cap
# that the user did not confirm, are folded into them. Measured against 1e1c041 after the shell item: core.js grew by 2,972
# with the rail groups, the rail counts, the icons, the view summary and Project activity, views_work.js by 148 with the
# summary of Now, and panel.css by 2,750 to 30,084. viewer.html grew by 2,221 to 8,677 with the 17 inline icon symbols, the
# foot of the rail and the header row. The user confirmed the page shell allowance of 2,300 on the same day.
# Measured against 07e03bf after the detail pane replaced the drawer: core.js lost 4,313 code characters and gained 6,365,
# a growth of 2,052 with the row selection, the J and K keys, the pinned foot, and the scroll positions and typed text that
# a live update keeps. The view files grew by 151 and panel.css lost 1,647 and gained 2,553, a growth of 906 to 30,990.
# Measured against e6f5550 after Now became tabs by kind with a list pane and a decision pane: views_work.js lost 5,016 code
# characters with the four cards, the attention rows and the list pane of Now, and gained 9,824, a growth of 4,808. core.js
# grew by 1,046 with Panel.listPane and Panel.paneRow, and panel.css lost 581 and gained 2,623, a growth of 2,042 to 33,032.
# The stylesheet allowance now uses 5,750 of its cap of 8,000 while four items that move views are still open.
# Measured against 92bced9 after Work and Plan moved into the frame with the work item in the pane: views_work.js lost
# 3,802 code characters with the table of Work, the heads of both views and their separate filter boxes, and gained 4,802
# with the list head, the rows of Work, its sort controls, the shared filter box and the pane foot of each view, a growth
# of 1,000. core.js gained 177 with the header sentences that name the list head and the pane body. panel.css lost 142
# and gained 682, a growth of 540 to 33,572, with the list head, the board as its own scrolling region and the wider
# summary of the header. The stylesheet allowance now uses 6,300 of its cap of 8,000 while three items that move views
# are still open.
REDESIGN_ALLOWANCE = {'views': 6_250, 'core.js': 6_400}
REDESIGN_LIMIT = 20_000
REDESIGN_STYLE_ALLOWANCE = 6_300
REDESIGN_STYLE_LIMIT = 8_000
REDESIGN_SHELL_ALLOWANCE = 2_300
ALLOWANCES = (FOCUS_ALLOWANCE, HIVE_ALLOWANCE, USAGE_ALLOWANCE, SESSIONS_ALLOWANCE, PANEL_ACTIONS_ALLOWANCE, USABILITY_ALLOWANCE,
              REDESIGN_ALLOWANCE)
TOTAL_SCRIPT_CHARACTERS = 196_000 + sum(sum(allowance.values()) for allowance in ALLOWANCES)
FILE_BUDGETS = {'core.js': 36_000 + sum(allowance.get('core.js', 0) for allowance in ALLOWANCES),
                'forms.js': 37_000 + sum(allowance.get('forms.js', 0) for allowance in ALLOWANCES), 'graphs.js': 41_000}
VIEW_CHARACTERS = 82_000 + sum(allowance.get('views', 0) for allowance in ALLOWANCES)
STYLE_CHARACTERS = 27_000 + USABILITY_STYLE_ALLOWANCE + REDESIGN_STYLE_ALLOWANCE
SHELL_CHARACTERS = 6_600 + REDESIGN_SHELL_ALLOWANCE


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

    def test_the_budget_is_measured_with_the_usage_view_in_place(self):
        # The usage allowance pays for the view, its host cards and its routing table, so the budget may not be met by removing them.
        self.assertLessEqual(sum(USAGE_ALLOWANCE.values()), USAGE_LIMIT)
        views = (UI / 'views_knowledge.js').read_text(encoding='utf-8')
        self.assertIn('P.registerView("usage"', views)
        self.assertIn('function usageCard(item, data)', views)
        self.assertIn('section("Routing decisions of recent runs"', views)

    def test_the_budget_is_measured_with_the_sessions_view_and_reconciliation_in_place(self):
        # These allowances pay for the Sessions view, the reconciliation card and the paged Now and board, so the budget
        # may not be met by removing them.
        self.assertLessEqual(sum(SESSIONS_ALLOWANCE.values()), SESSIONS_LIMIT)
        self.assertLessEqual(sum(PANEL_ACTIONS_ALLOWANCE.values()), PANEL_ACTIONS_LIMIT)
        forms = (UI / 'forms.js').read_text(encoding='utf-8')
        for name in ('session_flag', 'session_proposal', 'reconcile', 'reconcile_read_only'):
            self.assertIn(f'P.registerForm("{name}"', forms)
        self.assertIn('P.registerView("sessions"', (UI / 'views_knowledge.js').read_text(encoding='utf-8'))
        work = (UI / 'views_work.js').read_text(encoding='utf-8')
        # The tabs of Now replaced the list pane of the earlier Now view, and the decision pane serves its lessons, flags and proposals.
        self.assertIn('P.registerPane("decide"', work)
        self.assertIn('const NOW_EXTRA = { unconfirmed: "Needs reconciliation", kickoff: "Kickoff"', work)
        self.assertIn('async function unconfirmedCard(params)', work)

    def test_the_budget_is_measured_with_the_usability_changes_in_place(self):
        # The usability allowance pays for these parts, so the budget may not be met by removing them.
        self.assertLessEqual(sum(USABILITY_ALLOWANCE.values()), USABILITY_LIMIT)
        self.assertLessEqual(USABILITY_STYLE_ALLOWANCE, 400)
        work = (UI / 'views_work.js').read_text(encoding='utf-8')
        knowledge = (UI / 'views_knowledge.js').read_text(encoding='utf-8')
        for part in ('P.actButton = ', 'P.planPayload = ', 'function quickEdit(card, field, label, options)', 'dataset: { key: "work-folded" }',
                     'session_flags: ["review", "Session flags"'):
            self.assertIn(part, work)
        for part in ('function titledButton(id, options = {})', 'anchored("proposed", section("Proposed lessons"', '"Dismiss the " + flags.length + " shown flags"'):
            self.assertIn(part, knowledge)
        self.assertIn('context.needsPaths', (UI / 'forms.js').read_text(encoding='utf-8'))
        # The pane replaced the drawer that covered the page, so the stylesheet now hides the view behind a full width pane.
        self.assertIn('.shell[data-pane="open"] .main { visibility: hidden; }', (UI / 'panel.css').read_text(encoding='utf-8'))

    def test_the_budget_is_measured_with_the_fixed_frame_shell_in_place(self):
        # The redesign allowance is temporary and capped, and it pays for these parts of the shell.
        self.assertLessEqual(sum(REDESIGN_ALLOWANCE.values()), REDESIGN_LIMIT)
        self.assertLessEqual(REDESIGN_STYLE_ALLOWANCE, REDESIGN_STYLE_LIMIT)
        core = (UI / 'core.js').read_text(encoding='utf-8')
        for part in ('const WAITS = ', 'function icon(name)', 'function drawActivity()', 'const setSummary = ', 'function openPane(kind, params = {}, trigger)',
                     'function markSelected()', 'function stepRow(by)', 'const listPane = (options)', 'const paneRow = (key, name, title, sub, handler)'):
            self.assertIn(part, core)
        self.assertNotIn('Drawer', core)
        shell = (ROOT / 'viewer.html').read_text(encoding='utf-8')
        for part in ('<symbol id="i-usage"', 'class="rail-foot"', 'id="view-summary"', 'id="activity-toggle"'):
            self.assertIn(part, shell)
        work = (UI / 'views_work.js').read_text(encoding='utf-8')
        # Work and Plan fill the frame with a shared filter box, the rows of Work and the foot of each view.
        for part in ('ctx.setSummary(', 'const filterBox = (id, filtered, ...fields)', 'const paneBody = (name, ...children)', 'function listNode(cards, st)',
                     'selectControl("work-sort", "Sort by", COLUMNS'):
            self.assertIn(part, work)
        self.assertIn('.list-head {', (UI / 'panel.css').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
