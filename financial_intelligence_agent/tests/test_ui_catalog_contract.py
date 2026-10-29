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
    def test_local_secrets_not_committed(self):
        ignore=(ROOT/".gitignore").read_text()
        self.assertIn(".env",ignore)
        self.assertIn(".cache/",ignore)
        self.assertIn(".env",(ROOT/".dockerignore").read_text())
if __name__=="__main__":unittest.main()
