import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class CatalogUIContract(unittest.TestCase):
    def test_no_manual_key_or_curated_selector(self):
        text=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        for obsolete in ("Curated publisher series","studio_fred_key","Your FRED API key"):
            self.assertNotIn(obsolete,text)
        self.assertIn("global_series_search",text)
        self.assertIn("get_connection",text)
    def test_local_secrets_not_committed(self):
        gitignore=(ROOT/".gitignore").read_text()
        self.assertIn(".env",gitignore)
        self.assertIn(".cache/",gitignore)
        self.assertIn(".env",(ROOT/".dockerignore").read_text())
    def test_operation_chart_and_detail(self):
        text=(ROOT/"src/financial_intelligence_agent/live/studio.py").read_text()
        self.assertIn("aggregate_operations",text)
        self.assertIn("Operations underlying the current daily chart",text)
        self.assertIn("Download full publisher detail CSV",text)
if __name__=="__main__":unittest.main()
