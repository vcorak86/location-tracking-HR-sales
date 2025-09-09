
import pandas as pd
import re, unicodedata, uuid
from datetime import datetime

# -------- Helpers for header normalization --------
def _norm_header(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

HEADER_MAP = {
    # canonical -> candidates
    "Datum": ["datum","date","datum rada","datum unosa"],
    "Dan": ["dan","day"],
    "Ime i prezime": ["ime i prezime","imeiprezime","name","zaposlenik","employee"],
    "Odjel": ["odjel","department","odjeljenje","organizacijska jedinica","orgunit"],
    "Lokacija": ["lokacija","location","mjesto rada"],
    "Week": ["week","tjedan"],
    "Month": ["month","mjesec"],
    "Year": ["year","godina"],
    "date_iso": ["date iso","iso date","datum iso"]
}

def _build_reverse_map():
    rev = {}
    for canon, cands in HEADER_MAP.items():
        for c in cands:
            rev[_norm_header(c)] = canon
    # also map canonical itself
    for canon in HEADER_MAP:
        rev[_norm_header(canon)] = canon
    return rev

REV = _build_reverse_map()

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(HEADER_MAP.keys()))
    # drop duplicate columns by first occurrence
    df = df.loc[:, ~df.columns.duplicated()].copy()
    # rename by normalized headers
    newcols = {}
    for c in df.columns:
        canon = REV.get(_norm_header(c))
        newcols[c] = canon if canon else c
    t = df.rename(columns=newcols).copy()

    # ensure all expected columns exist
    for c in ["Datum","Dan","Ime i prezime","Odjel","Lokacija","Week","Month","Year","date_iso"]:
        if c not in t.columns:
            t[c] = ""

    # if date_iso empty, derive from Datum
    if t["date_iso"].isna().all() or (t["date_iso"].astype(str).str.strip() == "").all():
        s = t["Datum"].astype(str).str.strip().str.rstrip(".")
        t["date_iso"] = pd.to_datetime(s, dayfirst=True, errors="coerce").dt.date.astype("str")

    return t

def with_parsed_date(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Datum_dt","Godina"])
    t = normalize_columns(df).copy()
    s = t["Datum"].astype(str).str.strip().str.rstrip(".")
    t["Datum_dt"] = pd.to_datetime(s, dayfirst=True, errors="coerce")
    t["Godina"] = t["Datum_dt"].dt.year
    return t

# -------- Remote detection fallback --------
REMOTE_KEYS = {"remote","wfh","work from home","home office","rad od kuce","rad od kuće","kuci","kući","doma"}

def is_remote_value(s: str) -> bool:
    x = unicodedata.normalize("NFKD", str(s or ""))
    x = ''.join(ch for ch in x if not unicodedata.combining(ch))
    x = x.lower()
    return any(k in x for k in REMOTE_KEYS)

# -------- Stable keys --------
def record_key(name: str, date_iso: str) -> str:
    n = str(name or "").strip()
    d = str(date_iso or "").strip()
    return f"{n}|{d}"

def new_record_id(name: str, date_iso: str) -> str:
    ns = uuid.UUID('12345678-1234-5678-1234-567812345678')
    return str(uuid.uuid5(ns, record_key(name, date_iso)))

# -------- Canonical fields & dedupe --------
def apply_canonical_fields(df: pd.DataFrame, source: str = "app") -> pd.DataFrame:
    """
    Ensures required columns, derives date_iso, adds record_id/created_at/updated_at/version/source.
    Robust against missing headers and mixed schemas.
    """
    if df is None:
        return pd.DataFrame()
    t = normalize_columns(df).copy()

    # Derive/ensure date_iso again (in case of mixed types)
    s = t["date_iso"].astype(str).str.strip()
    need_iso = (s == "") | (s.str.lower() == "nan")
    if need_iso.any():
        src = t["Datum"].astype(str).str.strip().str.rstrip(".")
        t.loc[need_iso, "date_iso"] = pd.to_datetime(src, dayfirst=True, errors="coerce").dt.date.astype("str")

    # Safe Series for names/dates
    names = t["Ime i prezime"].astype(str).fillna("")
    dates = t["date_iso"].astype(str).fillna("")

    # row-wise generation to avoid length mismatches
    t["record_id"] = [
        new_record_id(n, d) for n, d in zip(names.tolist(), dates.tolist())
    ] if "record_id" not in t.columns else t["record_id"]

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    if "created_at" not in t.columns: t["created_at"] = now
    if "updated_at" not in t.columns: t["updated_at"] = now
    if "version" not in t.columns: t["version"] = 1
    if "source" not in t.columns: t["source"] = str(source or "app")

    # Reorder preferred columns first
    preferred = [
        "Datum","Dan","Ime i prezime","Odjel","Lokacija","Week","Month","Year",
        "date_iso","record_id","location_id","location_name","created_at","updated_at","source","version"
    ]
    cols = [c for c in preferred if c in t.columns] + [c for c in t.columns if c not in preferred]
    return t[cols]

def dedupe_last_then_sort_desc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Datum","Ime i prezime","date_iso"])
    t = normalize_columns(df).copy()
    # Ensure updated_at for ordering
    if "updated_at" not in t.columns:
        t["updated_at"] = ""
    # For stable ordering use original row index
    t["_row"] = range(len(t))
    # Sort so that latest (by date_iso, updated_at, row) comes first
    t = t.sort_values(["date_iso","updated_at","_row"], ascending=[False, False, False], kind="mergesort")
    # Keep first occurrence per (Ime i prezime, date_iso) → that's "last-wins"
    if "Ime i prezime" in t.columns and "date_iso" in t.columns:
        t = t.drop_duplicates(subset=["Ime i prezime","date_iso"], keep="first")
    # Final presentation ordering
    t = t.sort_values(["date_iso","Ime i prezime"], ascending=[False, True], kind="mergesort")
    return t.drop(columns=["_row"], errors="ignore")
