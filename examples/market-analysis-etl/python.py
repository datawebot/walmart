"""Run from any working directory; arguments resolve relative to that directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.workflows import main

if __name__ == '__main__':
    raise SystemExit(main('market-analysis-etl'))
