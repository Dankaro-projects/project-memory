"""Accessibility checks on the stylesheet of the control panel: contrast, visible focus and reduced motion.

The contrast of every text colour on the navy rail and on the teal buttons is computed with the formula of WCAG 2.1
from the values in panel.css, so a changed colour is measured again here. Measured on 22 September 2026: the lowest
text pair is white on the teal accent at 4.85 to 1, and no colour needed a change. The disabled primary button, white
on #8fc4c0 at 1.94 to 1, is an inactive control, which the rule 1.4.3 exempts; it is listed so that a change to it is
seen. The browser checks measure the focus stops and the key hints on the live panel.
"""
from pathlib import Path
import re
import unittest

CSS = re.sub(r'/\*.*?\*/', '', (Path(__file__).resolve().parents[1] / 'memory_module' / 'ui' / 'panel.css').read_text(encoding='utf-8'), flags=re.S)
VARIABLES = dict(re.findall(r'(--dk-[a-z-]+):\s*(#[0-9a-fA-F]{6})', CSS))
TEXT_PAIRS = (
    # (selector of the text colour, selector of the background, label). A plain hex value stands for a fixed background.
    ('.rail', '.rail', 'the link text of the rail'),
    ('.nav-more > summary', '.rail', 'the More views summary of the rail'),
    ('.nav-group h2', '.rail', 'the group headings of the rail'),
    ('.nav-omitted', '.rail', 'an omitted view in the rail of a scoped snapshot'),
    ('.nav-count', '.nav-count', 'the count badges of the rail'),
    ('.rail-foot strong', '.rail', 'the project name at the foot of the rail'),
    ('.phase-button', '.rail', 'the lifecycle stage at the foot of the rail'),
    ('.status[data-state="failed"]', '.rail', 'the failed status at the foot of the rail'),
    ('.nav-link[aria-current="page"]', '.nav-link[aria-current="page"]', 'the current view in the rail'),
    ('button.primary, .button.primary', 'button.primary, .button.primary', 'white on a teal button'),
    ('button.primary, .button.primary', 'button.primary:hover', 'white on a teal button under the pointer'),
    ('button.quiet, .button.quiet', '#ffffff', 'the accent text of a quiet button on white'),
    ('button.quiet, .button.quiet', 'button.quiet:hover', 'the accent text of a quiet button on the soft accent'),
    ('.detail-kind', '#ffffff', 'the kind label of the pane'),
    ('.tabs.view-tabs [aria-pressed="true"], .tabs.view-tabs [aria-selected="true"]',
     '.tabs.view-tabs [aria-pressed="true"], .tabs.view-tabs [aria-selected="true"]', 'the selected tab of a list view'),
    ('.tabs button', '#ffffff', 'a tab that is not selected'),
    ('.muted', '#ffffff', 'muted text on white'),
    ('.muted', '.notice', 'muted text on the surface of a notice'),
    ('input::placeholder, textarea::placeholder', '#ffffff', 'the placeholder of a field'),
)
FOCUS_PAIRS = (
    (':focus-visible', 'outline', '#ffffff', 'the focus outline on white'),
    ('.rail :focus-visible', 'outline-color', '.rail', 'the focus outline on the rail'),
)
# The only rules that remove the outline: two headings that take focus by script, the view pane, and the search input,
# whose wrapper shows the outline through .search:focus-within.
OUTLINE_REMOVED = {'#view-title', '#detail-title', '.main', '.search input'}


def declaration(selector, prop):
    """The value of a property in the rule whose selector list is exactly this text, with a variable resolved."""
    block = re.search(r'^' + re.escape(selector) + r'\s*\{([^}]*)\}', CSS, re.M)
    assert block, f'panel.css has no rule for {selector}'
    found = re.search(r'(?:^|;|\s)' + re.escape(prop) + r':\s*([^;]+)', block.group(1))
    assert found, f'the rule {selector} sets no {prop}'
    value = found.group(1).strip()
    var = re.search(r'var\((--dk-[a-z-]+)\)', value)
    if var:
        return VARIABLES[var.group(1)]
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
    """Text on the navy rail and on the teal buttons reaches a contrast of 4.5 to 1."""

    def test_the_text_colours_of_the_rail_and_the_buttons_reach_4_5_to_1(self):
        for text, back, label in TEXT_PAIRS:
            with self.subTest(label=label):
                front, behind = declaration(text, 'color'), background(back)
                self.assertGreaterEqual(contrast(front, behind), 4.5, f'{label}: {front} on {behind}')

    def test_the_lowest_text_pair_is_white_on_the_accent(self):
        ratios = {label: contrast(declaration(text, 'color'), background(back)) for text, back, label in TEXT_PAIRS}
        self.assertEqual(min(ratios, key=ratios.get), 'white on a teal button')
        self.assertAlmostEqual(min(ratios.values()), 4.85, places=2)

    def test_the_focus_outlines_reach_3_to_1_against_their_surface(self):
        for selector, prop, back, label in FOCUS_PAIRS:
            with self.subTest(label=label):
                self.assertGreaterEqual(contrast(declaration(selector, prop), background(back)), 3.0, label)

    def test_the_disabled_primary_button_is_the_only_exempt_pair(self):
        # An inactive control has no contrast requirement, and this test names it so that a change is noticed.
        self.assertLess(contrast(declaration('button.primary:disabled', 'color'), declaration('button.primary:disabled', 'background')), 4.5)


class FocusAndMotionTests(unittest.TestCase):
    """Every interactive element keeps the visible focus style, and reduced motion turns every transition off."""

    def test_the_focus_style_covers_every_interactive_element(self):
        self.assertIn(':focus-visible { outline: 2px solid var(--dk-color-accent); outline-offset: 2px; }', CSS)
        removed = {selector.strip() for selector, body in re.findall(r'([^{}]+)\{([^}]*)\}', CSS) if re.search(r'outline:\s*(none|0)\b', body)}
        self.assertEqual(removed, OUTLINE_REMOVED)
        self.assertIn('.search:focus-within { outline: 2px solid var(--dk-color-accent); outline-offset: 2px; }', CSS)
        for selector in ('.nav-link', '.nav-more > summary', '.pane-row', '.tabs button', '.detail-bar button'):
            for removed_selector in removed:
                self.assertNotIn(selector, removed_selector)

    def test_reduced_motion_turns_every_transition_and_animation_off(self):
        rule = re.search(r'@media \(prefers-reduced-motion: reduce\) \{ \*, ::before, ::after \{([^}]*)\} \}', CSS)
        self.assertTrue(rule, 'panel.css has no reduced motion rule')
        self.assertIn('transition: none !important;', rule.group(1))
        self.assertIn('animation: none !important;', rule.group(1))


if __name__ == '__main__':
    unittest.main()
