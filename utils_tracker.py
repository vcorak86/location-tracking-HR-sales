# utils_tracker.py
from __future__ import annotations
import re
import pandas as pd

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    ren={c: re.sub(r"\s+"," ", str(c).strip()).lower() for c in df.columns}
    t=df.rename(columns=ren)
    m={}
    for c in t.columns:
        if 'datum' in c: m[c]='Datum'
        elif ('ime' in c and 'prezime' in c) or c=='ime i prezime': m[c]='Ime i prezime'
        elif 'odjel' in c: m[c]='Odjel'
        elif 'lokacija' in c: m[c]='Lokacija'
        elif c=='week': m[c]='Week'
        elif c=='month': m[c]='Month'
        elif c=='year': m[c]='Year'
        else: m[c]=c
    t=t.rename(columns=m)
    if not t.columns.is_unique:
        t=t.loc[:, ~t.columns.duplicated(keep='first')]
    return t

def parse_date_flexible(x) -> pd.Timestamp:
    """
    Robust parsing for dates like:
    01.09.2025., 1.9.2025, 01/09/2025, 2025-09-01, ' 01.09.2025 .'
    Returns pandas.Timestamp (NaT if fails).
    """
    if x is None: return pd.NaT
    s=str(x).strip()
    # remove a trailing dot and extra spaces
    s=re.sub(r"\s+", " ", s)
    s=re.sub(r"\.\s*$", "", s)
    # try day-first then default
    for dayfirst in (True, False):
        dt = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce")
        if not pd.isna(dt): return dt
    return pd.NaT

def with_parsed_date(df: pd.DataFrame) -> pd.DataFrame:
    t=normalize_columns(df).copy()
    t['Datum_dt']=df.get('Datum', pd.Series([], dtype='object')).apply(parse_date_flexible)
    t['Godina']=t['Datum_dt'].dt.year
    t['Mjesec']=t['Datum_dt'].dt.month
    t['Kvartal']=((t['Mjesec']-1)//3 + 1)
    return t

def dedupe_last_then_sort_desc(df: pd.DataFrame) -> pd.DataFrame:
    t = normalize_columns(df).copy()
    # parsed + normalized textual fallback
    t["Datum_dt"] = t.get("Datum", pd.Series([], dtype='object')).apply(parse_date_flexible)

    def _norm_text_date(s: str) -> str:
        s = str(s).strip()
        s = re.sub(r"\s+", " ", s)
        s = re.sub(r"\.\s*$", "", s)
        return s

    t["Datum_txt_norm"] = t.get("Datum", "").astype(str).map(_norm_text_date)

    # key prefers parsed date
    t["Datum_key"] = t["Datum_dt"].dt.strftime("%Y-%m-%d")
    t.loc[t["Datum_key"].isna() | (t["Datum_key"] == "NaT"), "Datum_key"] = t["Datum_txt_norm"]

    if "Ime i prezime" in t.columns:
        t = t.drop_duplicates(subset=["Ime i prezime", "Datum_key"], keep="last")

    t = t.sort_values(["Datum_dt", "Datum_txt_norm"], ascending=[False, False], na_position="last")
    return t.drop(columns=["Datum_key", "Datum_txt_norm"], errors="ignore")

def is_remote_value(s)->bool:
    if not isinstance(s,str): return False
    l=s.lower()
    return ("remote" in l) or ("rad od ku" in l) or ("work from home" in l) or ("home office" in l)
