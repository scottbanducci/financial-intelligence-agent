import sys
import unittest
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Series,Point
from financial_intelligence_agent.engine.ml import make_supervised,walk_forward,unsupervised_analysis

class EstimatorIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dates=[date(2025,1,1)+timedelta(days=i) for i in range(100)]
        x=Series("x","TEST","unit","daily",tuple(Point(d,1+i*0.15) for i,d in enumerate(dates)))
        y=Series("y","TEST","unit","daily",tuple(Point(d,2+i*0.2) for i,d in enumerate(dates)))
        cls.data=make_supervised({"x":x},y,horizon=2)
    def test_ridge_is_better_than_constant_baseline(self):
        report=walk_forward(self.data,algorithm="ridge",initial_train=35,test_size=7,folds=3)
        self.assertEqual(report["model_metrics"]["n_test"],21)
        self.assertLess(report["model_metrics"]["mae"],report["baseline_metrics"]["mae"])
    def test_random_forest(self):
        report=walk_forward(self.data,algorithm="random_forest",initial_train=35,test_size=5,folds=2)
        self.assertEqual(len(report["splits"]),2)
    def test_unsupervised_models_execute(self):
        for algorithm in ("isolation_forest","pca","kmeans","dbscan"):
            with self.subTest(name=algorithm):
                output=unsupervised_analysis(self.data,algorithm=algorithm)
                self.assertEqual(output["observations"],len(self.data.features))
                self.assertIn("warning",output)

if __name__=="__main__":
    unittest.main()
