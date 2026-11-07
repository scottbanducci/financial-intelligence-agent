"""Entry point for manual or scheduled fixed-set provider refreshes.

Example: python scripts/refresh_live.py --source NYFED:SOFR --days 365 --out snapshots
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from financial_intelligence_agent.live.refresh import main
if __name__=="__main__":
    raise SystemExit(main())
