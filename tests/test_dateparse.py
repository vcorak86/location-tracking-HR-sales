# tests/test_dateparse.py
import pandas as pd
from utils_tracker import parse_date_flexible

def dt(d): 
    return None if pd.isna(d) else d.date()

def test_various_formats():
    samples = [
        ("01.09.2025.", (2025,9,1)),
        ("1.9.2025", (2025,9,1)),
        ("01/09/2025", (2025,9,1)),
        ("2025-09-01", (2025,9,1)),
        (" 01.09.2025 .", (2025,9,1)),
        ("not a date", None),
        ("", None),
    ]
    for s, expected in samples:
        ts = parse_date_flexible(s)
        if expected is None:
            assert pd.isna(ts)
        else:
            y,m,d = expected
            assert dt(ts) == pd.Timestamp(y,m,d).date()
