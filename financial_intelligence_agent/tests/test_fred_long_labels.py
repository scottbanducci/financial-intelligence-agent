"""FRED long-label hover behavior and HTML-injection safety contracts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))

from financial_intelligence_agent.live.long_labels import (
    metadata_html, _SCROLL_SCRIPT, _SCROLL_STYLE,
)


class FredLongLabelsTests(unittest.TestCase):
    def test_metadata_label_retains_unabridged_text_and_tooltip(self):
        long_label = ("Reserve balances at Federal Reserve Banks: "
                      "not seasonally adjusted, weekly Wednesday level; " * 5)
        rendered = metadata_html([{
            "id": "FRED:WRESBAL",
            "label": long_label,
            "frequency": "weekly",
            "unit": "Millions of USD",
        }])
        self.assertIn(long_label, rendered)
        self.assertIn('title="' + long_label + '"', rendered)
        self.assertIn('class="fred-label-viewport"', rendered)
        self.assertIn('tabindex="0"', rendered)
        self.assertIn("Millions of USD",rendered)
        self.assertIn("--fred-pan-duration:",rendered)
        self.assertIn("fred-label-text",rendered)

    def test_publisher_labels_are_escaped_in_text_and_attributes(self):
        attack = '<img src=x onerror="alert(1)">&'
        rendered = metadata_html([{"id":"FRED:BAD","label":attack,
                                  "frequency":"daily","unit":"percent"}])
        self.assertNotIn("<img",rendered)
        self.assertNotIn('onerror="alert(1)"',rendered)
        self.assertIn("&lt;img",rendered)
        self.assertIn("&quot;alert(1)&quot;",rendered)
        self.assertIn("&amp;",rendered)

    def test_metadata_table_caps_rendered_rows(self):
        content = metadata_html([
            {"id":f"FRED:ROW{i}","label":f"Data label {i}",
             "frequency":"daily","unit":"percent"}
            for i in range(100)
        ])
        self.assertEqual(content.count('<tr><td'),75)
        self.assertNotIn("FRED:ROW99",content)

    def test_dropdown_scroll_targets_only_series_options(self):
        self.assertIn('aria-label="Series"',_SCROLL_SCRIPT)
        self.assertIn("[data-item-hl]",_SCROLL_SCRIPT)
        self.assertIn("pointerover",_SCROLL_SCRIPT)
        self.assertIn("pointerout",_SCROLL_SCRIPT)
        self.assertIn("scrollWidth - target.clientWidth",_SCROLL_SCRIPT)
        self.assertIn("prefers-reduced-motion",_SCROLL_SCRIPT)
        self.assertIn("[role=\"listbox\"][aria-label=\"Series\"]",_SCROLL_STYLE)
        self.assertIn("100cqw - 100%",_SCROLL_STYLE)
        self.assertIn("fred-label-pan",_SCROLL_STYLE)

if __name__ == "__main__":
    unittest.main()
