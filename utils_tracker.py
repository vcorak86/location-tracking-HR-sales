
from __future__ import annotations
import re, uuid, unicodedata
from datetime import datetime
import pandas as pd

def _norm_colname(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["Datum","Dan","Ime i prezime","Odjel","Lokacija","Week","Month","Year"])
    t = df.copy()
    t.columns = [_norm_colname(c) for c in t.columns]
    ren = {}
    for c in list(t.columns):
        lc = c.lower()
        if lc in ("ime i prezime","ime i prezime","name","employee","zaposlenik"):
            ren[c] = "Ime i prezime"
        elif lc.startswith("odjel") or lc.startswith("department"):
            ren[c] = "Odjel"
        elif lc.startswith("lokacija") or lc.startswith("location"):
            ren[c] = "Lokacija"
        elif lc == "datum" or lc == "date":
            ren[c] = "Datum"
        elif lc == "dan" or lc == "day":
            ren[c] = "Dan"
        elif lc.startswith("week") or lc.startswith("tjedan"):
            ren[c] = "Week"
        elif lc.startswith("month") or lc.startswith("mjesec"):
            ren[c] = "Month"
        elif lc.startswith("year") or lc.startswith("godina"):
            ren[c] = "Year"
    if ren: t = t.rename(columns=ren)
    for col in ["Datum","Dan","Ime i prezime","Odjel","Lokacija","Week","Month","Year"]:
        if col not in t.columns:
            t[col] = ""
    return t

def _strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def with_parsed_date(df: pd.DataFrame) -> pd.DataFrame:
    t = normalize_columns(df).copy()
    s = t["Datum"].astype(str).str.strip().str.rstrip(".")
    dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
    t["Datum_dt"] = dt
    t["date_iso"] = t["Datum_dt"].dt.date.astype("str")
    t["Godina"] = t["Datum_dt"].dt.isocalendar().year.astype("Int64")
    t["Mjesec"] = t["Datum_dt"].dt.month.astype("Int64")
    t["Kvartal"] = ((t["Mjesec"] - 1) // 3 + 1).astype("Int64")
    t["Week"] = pd.to_numeric(t["Week"], errors="coerce").astype("Int64").where(t["Week"].notna(), t["Datum_dt"].dt.isocalendar().week.astype("Int64"))
    t["Month"] = pd.to_numeric(t["Month"], errors="coerce").astype("Int64").where(t["Month"].notna(), t["Mjesec"])
    t["Year"] = pd.to_numeric(t["Year"], errors="coerce").astype("Int64").where(t["Year"].notna(), t["Godina"])
    return t

def is_remote_value(s: str) -> bool:
    x = _strip_accents(str(s or "")).lower()
    keys = ["remote","wfh","work from home","home office","rad od kuce","rad od kuće","kuci","kući","doma"]
    return any(k in x for k in keys)

def record_key(name: str, date_iso: str) -> str:
    return f"{str(name or '').strip()}|{str(date_iso or '').strip()}"

def new_record_id(name: str, date_iso: str) -> str:
    ns = uuid.UUID("12345678-1234-5678-1234-567812345678")
    return str(uuid.uuid5(ns, record_key(name, date_iso)))

def apply_canonical_fields(df: pd.DataFrame, source: str = "app") -> pd.DataFrame:
    t = with_parsed_date(df).copy()
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for col in ["Ime i prezime","date_iso"]:
        if col not in t.columns:
            t[col] = ""
    names = t["Ime i prezime"].astype(str).fillna("")
    dates = t["date_iso"].astype(str).fillna("")
    if "record_id" not in t.columns:
        t["record_id"] = [new_record_id(n, d) for n, d in zip(names, dates)]
    if "created_at" not in t.columns:
        t["created_at"] = now
    if "updated_at" not in t.columns:
        t["updated_at"] = now
    else:
        t["updated_at"] = t["updated_at"].fillna(now).replace("", now)
    if "version" not in t.columns:
        t["version"] = 1
    if "source" not in t.columns:
        t["source"] = source
    return t

def dedupe_last_then_sort_desc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    t = with_parsed_date(df).copy()
    if "updated_at" not in t.columns:
        t["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    t = t.sort_values(["date_iso","updated_at"], ascending=[False, False], kind="mergesort")
    if "Ime i prezime" in t.columns and "date_iso" in t.columns:
        t = t.drop_duplicates(subset=["Ime i prezime","date_iso"], keep="first")
    # final presentation: global DESC by date
    t = t.sort_values(["date_iso","Ime i prezime"], ascending=[False, True], kind="mergesort")
    return t

def validate_tracker_schema(df: pd.DataFrame) -> None:
    required = ["Datum","Dan","Ime i prezime","Odjel","Lokacija","Week","Month","Year","date_iso"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Nedostaju obavezne kolone u Tracker: {missing}")
