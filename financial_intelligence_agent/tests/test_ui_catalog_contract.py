import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CatalogUIContract(unittest.TestCase):
    def test_source_tabs_and_no_endpoint_forms(self):
        text=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        self.assertIn('["FRED","NY Fed","FDIC',text)
        for old in ('"FRED + NY Fed rates"','"Primary dealers","Other NY Fed APIs"',
                    "Curated publisher series","studio_fred_key"):
            self.assertNotIn(old,text)
        self.assertIn("fred_release_selected",text)
        self.assertIn("nyfed_dataset_search",text)
        self.assertIn("Download complete underlying records CSV",text)
    def test_live_source_page_stays_focused_on_discovery(self):
        studio=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        live=studio.split("def render():",1)[1].split("def render_transformations():",1)[0]
        self.assertIn("_load_fred()",live)
        self.assertIn("_load_nyfed()",live)
        self.assertNotIn("_chart_builder()",live)
        self.assertNotIn("Working data catalog",live)
        self.assertNotIn("_operators()",live)
        self.assertIn("preview_figure(series,label)",studio)
        self.assertIn("_source_publication({recent:series}",studio)
    def test_operator_first_page_contains_chart_and_exports(self):
        studio=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        page=studio.split("def render_transformations():",1)[1]
        self.assertIn("_operators()",page)
        self.assertIn("_chart_builder()",page)
        self.assertLess(page.index("_operators()"),page.index("_chart_builder()"))
        self.assertIn("_workspace_management()",page)
        self.assertIn("_publication_buttons(frame,spec,unit,chosen,warnings",studio)
        app=(ROOT/"app.py").read_text()
        self.assertIn('"Data transformations"',app)
        self.assertIn('render_transformations()',app)
    def test_fred_discontinued_toggle_and_quiet_preview(self):
        studio=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        self.assertIn('fred_include_discontinued',studio)
        self.assertIn('include_discontinued=include_discontinued',studio)
        self.assertNotIn("Y-axis automatically follows the observed range rather than starting at zero.",studio)

    def test_global_dataset_scrolling_all_workspaces(self):
        app=(ROOT/"app.py").read_text()
        studio=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        self.assertIn("install_dataset_label_scrolling()",app)
        self.assertIn("render_dataset_table(results",app)
        self.assertIn("render_dataset_table(found_releases",app)
        self.assertIn("render_dataset_table(summary",studio)
        self.assertIn("render_dataset_table([",app)
        self.assertIn("format_func=lambda key:f\"{ws[key]['descriptor'].label}",studio)
        self.assertNotIn("'[:220]",studio)

    def test_local_secrets_not_committed(self):
        ignore=(ROOT/".gitignore").read_text()
        self.assertIn(".env",ignore)
        self.assertIn(".cache/",ignore)
        self.assertIn(".env",(ROOT/".dockerignore").read_text())
if __name__=="__main__":unittest.main()
