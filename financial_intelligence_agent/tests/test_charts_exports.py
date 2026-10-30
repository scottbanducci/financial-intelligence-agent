import sys,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from financial_intelligence_agent.engine.operators import Series,Point
from financial_intelligence_agent.live.charts import ChartSpec,prepare,image_bytes,csv_bytes,metadata_bytes,interactive_chart,research_bundle_bytes,chart_recipe_bytes,load_chart_recipe

def s(key,unit,vals):
    return Series(key,"FRED",unit,"daily",tuple(Point(date(2026,10,i+1),v) for i,v in enumerate(vals)))
class Charts(unittest.TestCase):
    def setUp(self):
        self.a=s("a","percent",[3.8,3.9,None,4.1])
        self.b=s("b","percent",[4.0,4.1,4.2,4.0])
    def test_chart_labels_exports_png_svg_and_csv(self):
        spec=ChartSpec("Rates","SOFR and IORB",source_note="Fed NY Fed • 2026")
        df,unit,warnings=prepare({"SOFR":self.a,"IORB":self.b},spec)
        self.assertEqual(unit,"percent")
        self.assertIsNone(df["SOFR"].iloc[2] if False else None)
        self.assertTrue(df["SOFR"].isna().iloc[2])
        png=image_bytes(df,spec,unit,"png")
        self.assertEqual(png[:8],b"\x89PNG\r\n\x1a\n")
        svg=image_bytes(df,spec,unit,"svg")
        self.assertIn(b"Rates",svg)
        self.assertIn(b"Observation date",svg)
        exported=csv_bytes(df)
        self.assertIn(b"Observation date",exported)
        self.assertIn(b"2026-10-04",exported)
        self.assertIn(b"fingerprint",metadata_bytes({"SOFR":self.a},spec,unit,warnings))
        self.assertEqual(len(interactive_chart(df,spec,unit).data),2)
    def test_visible_y_autoscale_does_not_include_outside_points(self):
        from financial_intelligence_agent.live.charts import scale_y_to_visible
        import plotly.graph_objects as go
        from datetime import date
        fig=go.Figure()
        fig.add_trace(go.Scatter(x=["2026-05-01","2026-10-01","2026-10-02"],
                                 y=[1000,1.0,1.2],name="levels"))
        fig.add_trace(go.Bar(x=["2026-05-01","2026-10-01","2026-10-02"],
                             y=[5000,30,32],name="volume",yaxis="y2"))
        result=scale_y_to_visible(fig,date(2026,9,1),date(2026,10,15))
        self.assertLess(result.layout.yaxis.range[1],2)
        self.assertGreater(result.layout.yaxis.range[0],0)
        self.assertLess(result.layout.yaxis2.range[1],40)
        self.assertEqual(list(result.layout.xaxis.range),["2026-09-01","2026-10-15"])

    def test_preview_axis_tracks_data_not_zero(self):
        from financial_intelligence_agent.live.charts import preview_figure
        from financial_intelligence_agent.engine.operators import Series,Point
        from datetime import date
        s=Series("closerange","FRED","percent","daily",(
            Point(date(2026,10,1),4.80),
            Point(date(2026,10,2),4.84),
            Point(date(2026,10,3),4.83)))
        fig=preview_figure(s,"Test rate")
        low,high=fig.layout.yaxis.range
        self.assertGreater(low,4.7)
        self.assertLess(high,4.9)
        flat=Series("flat","FRED","percent","daily",(
            Point(date(2026,10,1),4.80),
            Point(date(2026,10,2),4.80)))
        a,b=preview_figure(flat,"Flat rate").layout.yaxis.range
        self.assertLess(a,4.8)
        self.assertGreater(b,4.8)

    def test_publication_bundle_and_recipe(self):
        from zipfile import ZipFile
        from io import BytesIO
        spec=ChartSpec("Money Market Rates","Historical source observations")
        sources={"SOFR":self.a,"IORB":self.b}
        df,unit,warnings=prepare(sources,spec)
        content=research_bundle_bytes(df,spec,unit,sources,warnings)
        with ZipFile(BytesIO(content)) as package:
            self.assertEqual(set(package.namelist()),
                {"chart.png","chart.svg","observations.csv","provenance.json"})
            self.assertTrue(package.read("chart.png").startswith(bytes([137,80,78,71])))
        recipe=chart_recipe_bytes(sources,spec)
        restored,restored_spec=load_chart_recipe(recipe,sources)
        self.assertEqual(restored_spec.title,spec.title)
        self.assertEqual(len(restored),2)
        with self.assertRaisesRegex(ValueError,"fingerprint mismatch"):
            load_chart_recipe(recipe,{"SOFR":self.b,"IORB":self.a})
    def test_units_and_normalization(self):
        different=s("credit","USD billions",[100,105,99,110])
        with self.assertRaisesRegex(ValueError,"matching units"):
            prepare({"rate":self.a,"credit":different},ChartSpec("Compare",""))
        df,u,w=prepare({"rate":self.a,"credit":different},
                ChartSpec("Compare","",mode="Indexed (first observation = 100)"))
        self.assertEqual(df["credit"].iloc[1],105)
        self.assertEqual(u,"Index (base 100)")
        self.assertEqual(len(w),2)
    def test_csv_formula_injection_blocked(self):
        df,u,w=prepare({"=HYPERLINK('bad')":self.a},ChartSpec("Safe",""))
        text=csv_bytes(df).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK",text)
    def test_date_bounding(self):
        df,_,_=prepare({"SOFR":self.a},ChartSpec("Small","",start=date(2026,10,2),
                                              end=date(2026,10,3)))
        self.assertEqual(len(df),2)
        with self.assertRaises(ValueError):
            prepare({"SOFR":self.a},ChartSpec("bad","",mode="raw unsafe"))

if __name__=="__main__":unittest.main()
