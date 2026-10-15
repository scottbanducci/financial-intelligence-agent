"""Offline contract checks for the FRED Silver notebook.

These tests inspect the notebook's transformation structure without running Spark.
They are NOT a substitute for the Databricks pipeline integration validation in
docs/silver_revision_validation.sql.
"""
import json
import unittest
from pathlib import Path


NOTEBOOK = Path(__file__).resolve().parents[1] / "src" / "fredpipe.ipynb"


def _silver_code():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    code = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell.get("cell_type") == "code"
    )
    return code.split("def fred_observations():", 1)[1].split("@dlt.table(", 1)[0]


class FredSilverRevisionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.silver = _silver_code()

    def test_observations_not_filtered_before_revision_ranking(self):
        self.assertNotIn('.where(F.col("obs.value") != ".")', self.silver)
        self.assertNotIn('.filter(F.col("obs.value") != ".")', self.silver)
        self.assertIn('F.posexplode("response.observations")', self.silver)
        self.assertIn('F.expr("try_cast(obs.value AS double)")', self.silver)

    def test_latest_record_selected_before_output(self):
        self.assertIn('Window.partitionBy("series_id", "observation_date")', self.silver)
        self.assertIn('F.row_number().over(latest_first)', self.silver)
        self.assertIn('.where(F.col("_rn") == 1)', self.silver)
        self.assertLess(
            self.silver.index('F.row_number().over(latest_first)'),
            self.silver.index('.where(F.col("_rn") == 1)'),
        )

    def test_timestamps_have_deterministic_tiebreakers(self):
        self.assertIn('F.col("fetched_at").desc_nulls_last()', self.silver)
        self.assertIn('F.col("_source_file").desc_nulls_last()', self.silver)
        self.assertIn('F.col("_obs_index").desc()', self.silver)

    def test_missing_latest_dropped_by_post_selection_expectation(self):
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        code = "\n".join(
            "".join(cell.get("source", []))
            for cell in notebook["cells"]
            if cell.get("cell_type") == "code"
        )
        prefix = code.split("def fred_observations():", 1)[0]
        self.assertIn('"value_is_numeric":       "value IS NOT NULL"', prefix)
        self.assertIn('parsed_value / 1000', self.silver)
        self.assertIn('.drop("_rn", "_source_file", "_obs_index")', self.silver)


if __name__ == "__main__":
    unittest.main()
