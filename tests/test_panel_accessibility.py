"""Accessibility checks on the stylesheet of the control panel: contrast, visible focus and reduced motion.

The contrast of every text colour on the light sidebar, on the page, on a callout and on the blue primary button is
computed with the formula of WCAG 2.1 from the values in panel.css, so a changed colour is measured again here. The
status pills are measured in the light and in the dark theme. Measured on 24 September 2026, after the move to the
colours of Notion: the grey of muted text was darkened from #787774 (4.14 to 1 on a callout) to #6f6e69, the blue of
the primary button from #2383e2 (3.88 to 1 under white) to #1f6fc5, and the red of a danger button took its own
token #b3372c, because the red of a status dot reached 3.15 to 1. The lowest text pair is now muted text on a callout at
4.73 to 1. The disabled primary button is an inactive control, which the rule 1.4.3 exempts; it is listed so that a
change to it is seen. The browser checks measure the focus stops on the live panel.
"""
from pathlib import Path
import re
import unittest

CSS = re.sub(r'/\*.*?\*/', '', (Path(__file__).resolve().parents[1] / 'memory_module' / 'ui' / 'panel.css').read_text(encoding='utf-8'), flags=re.S)
# The first :root block holds the light theme; the dark theme redefines the same names inside a media query.
LIGHT = dict(re.findall(r'(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})', re.search(r'^:root \{(.*?)^\}', CSS, re.S | re.M).group(1)))
DARK = dict(re.findall(r'(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})', re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}', CSS, re.S).group(1)))
TEXT_PAIRS = (
    # (selector of the text colour, selector of the background, label). A plain hex value stands for a fixed background.
    ('.rail', '.rail', 'the link text of the sidebar'),
    ('.nav-group h2', '.rail', 'the section headings of the sidebar'),
    ('.nav-omitted', '.rail', 'an omitted view in the sidebar of a scoped snapshot'),
    ('.workspace strong', '.rail', 'the project name of the sidebar'),
    ('.status', '.rail', 'the live state at the foot of the sidebar'),
    ('.status[data-state="failed"]', '.rail', 'the failed status at the foot of the sidebar'),
    ('button.primary, .button.primary', 'button.primary, .button.primary', 'white on a blue button'),
    ('button.primary, .button.primary', 'button.primary:hover', 'white on a blue button under the pointer'),
    ('button.quiet, .button.quiet', '#ffffff', 'the text of a quiet button'),
    ('button.danger', '#ffffff', 'the text of a danger button'),
    ('.crumbs', '#ffffff', 'the breadcrumbs'),
    ('.detail-kind', '#ffffff', 'the kind label of the side peek'),
    ('.tabs button', '#ffffff', 'a tab that is not selected'),
    ('.db-view', '#ffffff', 'a view of a database that is not selected'),
    ('.db-tool.active', '#ffffff', 'a database tool in use'),
    ('.muted', '#ffffff', 'muted text on white'),
    ('.muted', '.notice', 'muted text on a callout'),
    ('input::placeholder, textarea::placeholder', '#ffffff', 'the placeholder of a field'),
)
TONES = ('red', 'orange', 'yellow', 'green', 'blue', 'purple', 'gray', 'plain')
FOCUS_PAIRS = (
    (':focus-visible', 'outline', '#ffffff', 'the focus outline on white'),
    (':focus-visible', 'outline', '.rail', 'the focus outline on the sidebar'),
)
# The rules that remove the outline: two headings that take focus by script and the view pane, and the fields, which
# show their focus as a ring of 2 pixels in the accent instead: every field, the search of the sidebar through its
# wrapper, and the search of a database.
OUTLINE_REMOVED = {'#view-title', '#detail-title', '.main', 'input:focus, select:focus, textarea:focus', '.search input'}
RINGS = ('input:focus, select:focus, textarea:focus', '.search:focus-within', '.db-search input:focus', ':is(.kn-filter-box, .list-head) .field:focus-within')


def declaration(selector, prop, variables=LIGHT):
    """The value of a property in the rule whose selector list is exactly this text, with a variable resolved."""
    block = re.search(r'^' + re.escape(selector) + r'\s*\{([^}]*)\}', CSS, re.M)
    assert block, f'panel.css has no rule for {selector}'
    found = re.search(r'(?:^|;|\s)' + re.escape(prop) + r':\s*([^;]+)', block.group(1))
    assert found, f'the rule {selector} sets no {prop}'
    value = found.group(1).strip()
    var = re.search(r'var\((--[a-z0-9-]+)\)', value)
    if var:
        return variables[var.group(1)]
    colour = re.search(r'#[0-9a-fA-F]{6}', value)
    assert colour, f'{selector} {prop} is not a hex colour: {value}'
    return colour.group(0)


def background(selector):
    return selector if selector.startswith('#') else declaration(selector, 'background')


def luminance(colour):
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    red, green, blue = (int(colour[index:index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(front, back):
    """The contrast ratio of WCAG 2.1, from 1 to 21."""
    first, second = sorted((luminance(front), luminance(back)), reverse=True)
    return (first + 0.05) / (second + 0.05)


class ContrastTests(unittest.TestCase):
    """Text on the sidebar, the page, a callout and the blue button, and every status pill, reach 4.5 to 1."""

    def test_the_text_colours_reach_4_5_to_1(self):
        for text, back, label in TEXT_PAIRS:
            with self.subTest(label=label):
                front, behind = declaration(text, 'color'), background(back)
                self.assertGreaterEqual(contrast(front, behind), 4.5, f'{label}: {front} on {behind}')

    def test_the_lowest_text_pair_is_muted_text_on_a_callout(self):
        ratios = {label: contrast(declaration(text, 'color'), background(back)) for text, back, label in TEXT_PAIRS}
        self.assertEqual(min(ratios, key=ratios.get), 'muted text on a callout')
        self.assertAlmostEqual(min(ratios.values()), 4.73, places=2)

    def test_every_status_pill_reaches_4_5_to_1_in_both_themes(self):
        for theme, variables in (('light', LIGHT), ('dark', DARK)):
            for tone in TONES:
                with self.subTest(theme=theme, tone=tone):
                    self.assertGreaterEqual(contrast(variables[f'--{tone}-text'], variables[f'--{tone}-bg']), 4.5)

    def test_the_focus_outlines_reach_3_to_1_against_their_surface(self):
        for selector, prop, back, label in FOCUS_PAIRS:
            with self.subTest(label=label):
                self.assertGreaterEqual(contrast(declaration(selector, prop), background(back)), 3.0, label)

    def test_the_disabled_primary_button_is_the_only_exempt_pair(self):
        # An inactive control has no contrast requirement, and this test names it so that a change is noticed.
        self.assertLess(contrast(declaration('button.primary:disabled', 'color'), declaration('button.primary:disabled', 'background')), 4.5)


class FocusAndMotionTests(unittest.TestCase):
    """Every interactive element keeps a visible focus style, and reduced motion turns every transition off."""

    def test_the_focus_style_covers_every_interactive_element(self):
        self.assertIn(':focus-visible { outline: 2px solid var(--accent);', CSS)
        removed = {selector.strip() for selector, body in re.findall(r'([^{}]+)\{([^}]*)\}', CSS) if re.search(r'outline:\s*(none|0)\b', body)}
        self.assertEqual(removed, OUTLINE_REMOVED)
        for selector in RINGS:
            with self.subTest(selector=selector):
                self.assertRegex(CSS, re.escape(selector) + r'\s*\{[^}]*box-shadow: inset 0 0 0 2px var\(--accent\)')
        for selector in ('.nav-link', '.nav-more > summary', '.pane-row', '.tabs button', '.detail-bar button', '.db-open', '.db-view', '.db-tool', '.menu-item'):
            for removed_selector in removed:
                self.assertNotIn(selector, removed_selector)

    def test_reduced_motion_turns_every_transition_and_animation_off(self):
        rule = re.search(r'@media \(prefers-reduced-motion: reduce\) \{ \*, ::before, ::after \{([^}]*)\} \}', CSS)
        self.assertTrue(rule, 'panel.css has no reduced motion rule')
        self.assertIn('transition: none !important;', rule.group(1))
        self.assertIn('animation: none !important;', rule.group(1))


if __name__ == '__main__':
    unittest.main()
