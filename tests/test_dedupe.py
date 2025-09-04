# tests/test_dedupe.py
import pandas as pd
from utils_tracker import dedupe_last_then_sort_desc

def test_last_wins_and_sort_desc():
    df = pd.DataFrame([
        {"Datum":"01.09.2025.", "Ime i prezime":"Ana A", "Lokacija":"Ured"},
        {"Datum":"1.9.2025",   "Ime i prezime":"Ana A", "Lokacija":"Remote"},   # same logical day, should win
        {"Datum":"02.09.2025.", "Ime i prezime":"Ana A", "Lokacija":"Ured"},
        {"Datum":"03/09/2025", "Ime i prezime":"Ana A", "Lokacija":"Na terenu"},
        {"Datum":"2025-09-04", "Ime i prezime":"Ana A", "Lokacija":"Ured"},
        {"Datum":"04.09.2025.", "Ime i prezime":"Ana A", "Lokacija":"Remote"},  # same logical day, should win
    ])
    out = dedupe_last_then_sort_desc(df)
    # Expect unique days: 1st, 2nd, 3rd, 4th -> 4 rows
    assert len(out) == 4
    # last wins for 01.09 (Remote) and 04.09 (Remote)
    row_1 = out[out["Datum"].str.contains("01.09")].iloc[0]
    assert row_1["Lokacija"].lower() == "remote"
    row_4 = out[out["Datum"].str.contains("04.09")].iloc[0]
    assert row_4["Lokacija"].lower() == "remote"
    # sort desc: first row should be 04.09
    first = out.iloc[0]
    assert "04.09" in first["Datum"]
