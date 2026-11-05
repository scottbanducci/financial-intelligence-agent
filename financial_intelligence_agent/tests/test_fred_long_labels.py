"""FRED long-label hover behavior and HTML-injection safety contracts."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT / "src"))

from financial_intelligence_agent.live.long_labels import (
    metadata_html, dataset_table_html, _SCROLL_SCRIPT, _SCROLL_STYLE,
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
        self.assertIn('aria-label="' + long_label + '"', rendered)
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

    def test_global_table_reveals_all_text_and_safely_escapes(self):
        full="Very long publisher research dataset description "*20
        sample=[{"id":"NYFED_PD:LONG","label":full,
                 "source_url":'<script>alert("x")</script>',"count":12}]
        rendered=dataset_table_html(sample,
                     ["id","label","source_url","count"],max_rows=100)
        self.assertIn('class="dataset-label-viewport"',rendered)
        self.assertIn('aria-label="'+full+'"',rendered)
        self.assertIn("&lt;script&gt;",rendered)
        self.assertNotIn("<script>",rendered)
        self.assertEqual(rendered.count('dataset-label-viewport'),4)
    def test_global_table_limits_row_count_and_bounds(self):
        rows=[{"name":"Row "+str(i)} for i in range(40)]
        rendered=dataset_table_html(rows,["name"],max_rows=12,height=280)
        self.assertEqual(rendered.count('<tr><td'),12)
        self.assertNotIn("Row 39",rendered)
        with self.assertRaises(ValueError):
            dataset_table_html(rows,["name"],max_rows=0)
        with self.assertRaises(ValueError):
            dataset_table_html(rows,["name"],height=10)
    def test_global_selector_and_selected_value_behavior(self):
        self.assertIn('[data-testid="stSelectbox"] input',_SCROLL_SCRIPT)
        self.assertIn('[data-testid="stMultiSelect"]',_SCROLL_SCRIPT)
        self.assertIn('.dataset-label-viewport',_SCROLL_SCRIPT)
        self.assertIn("window.__datasetLongLabelScrollInstalled",_SCROLL_SCRIPT)
        self.assertIn('prefers-reduced-motion',_SCROLL_SCRIPT)

    def test_plotly_visible_y_autoscale_handler_installed(self):
        from financial_intelligence_agent.live.long_labels import _VISIBLE_Y_AUTOSCALE
        self.assertIn("modebar-btn",_VISIBLE_Y_AUTOSCALE)
        self.assertIn('data-title="Autoscale"',_VISIBLE_Y_AUTOSCALE)
        self.assertIn("event.stopImmediatePropagation()",_VISIBLE_Y_AUTOSCALE)
        self.assertIn("Plotly.relayout(gd,changes)",_VISIBLE_Y_AUTOSCALE)
        self.assertIn("gd.calcdata",_VISIBLE_Y_AUTOSCALE)
        self.assertIn("xaxis",_VISIBLE_Y_AUTOSCALE)
        self.assertIn('yaxis',_VISIBLE_Y_AUTOSCALE)

    def test_dropdown_scroll_targets_only_series_options(self):
        self.assertIn("closest('[role=\"listbox\"]')",_SCROLL_SCRIPT)
        self.assertIn("[data-item-hl]",_SCROLL_SCRIPT)
        self.assertIn("pointerover",_SCROLL_SCRIPT)
        self.assertIn("pointerout",_SCROLL_SCRIPT)
        self.assertIn("scrollWidth - target.clientWidth",_SCROLL_SCRIPT)
        self.assertIn("prefers-reduced-motion",_SCROLL_SCRIPT)
        self.assertIn('[role="listbox"] [role="option"]',_SCROLL_STYLE)
        self.assertIn("fred-label-viewport",_SCROLL_STYLE)
        self.assertIn('node.closest(".fred-label-viewport, .dataset-label-viewport")',_SCROLL_SCRIPT)

if __name__ == "__main__":
    unittest.main()
