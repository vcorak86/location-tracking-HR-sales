# scripts/normalize_tracker.py
from pathlib import Path
import pandas as pd
from utils_tracker import dedupe_last_then_sort_desc

TRACKER_PATH = Path("data/Tracker.csv")

def main():
    if not TRACKER_PATH.exists():
        print("No data/Tracker.csv to normalize; skipping.")
        return 0
    df = pd.read_csv(TRACKER_PATH, sep=None, engine="python")
    out = dedupe_last_then_sort_desc(df)
    pref=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year']
    cols=[c for c in pref if c in out.columns]+[c for c in out.columns if c not in pref]
    out = out[cols]
    out.to_csv(TRACKER_PATH, index=False)
    print("Normalized Tracker.csv (DESC + last-wins).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
