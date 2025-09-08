
from __future__ import annotations
import re, uuid
from datetime import datetime
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
        elif 'dan'==c: m[c]='Dan'
        elif c in ('record_id','created_at','updated_at','source','version','date_iso'): m[c]=c
        else: m[c]=c
    t=t.rename(columns=m)
    if not t.columns.is_unique:
        t=t.loc[:, ~t.columns.duplicated(keep='first')]
    return t

def parse_date_flexible(x) -> pd.Timestamp:
    if x is None: return pd.NaT
    s=str(x).strip()
    s=re.sub(r"\s+"," ", s)
    s=re.sub(r"\.\s*$", "", s)
    for dayfirst in (True, False):
        dt = pd.to_datetime(s, dayfirst=dayfirst, errors="coerce")
        if not pd.isna(dt): return dt
    return pd.NaT

def with_parsed_date(df: pd.DataFrame) -> pd.DataFrame:
    t=normalize_columns(df).copy()
    if 'date_iso' not in t.columns:
        t['Datum_dt']=t.get('Datum', pd.Series([], dtype='object')).apply(parse_date_flexible)
        t['date_iso']=t['Datum_dt'].dt.strftime('%Y-%m-%d')
    else:
        t['Datum_dt']=pd.to_datetime(t['date_iso'], errors='coerce')
    t['Godina']=t['Datum_dt'].dt.year
    t['Mjesec']=t['Datum_dt'].dt.month
    t['Kvartal']=((t['Mjesec']-1)//3 + 1)
    return t

def record_key(name:str, date_iso:str)->str:
    return f"{name.strip()}|{date_iso}"

def new_record_id(name:str, date_iso:str)->str:
    ns=uuid.UUID('12345678-1234-5678-1234-567812345678')
    return str(uuid.uuid5(ns, record_key(name, date_iso)))

def apply_canonical_fields(df: pd.DataFrame, source:str="app") -> pd.DataFrame:
    t=normalize_columns(df).copy()
    if 'date_iso' not in t.columns:
        dt=t.get('Datum', pd.Series([], dtype='object')).apply(parse_date_flexible)
        t['date_iso']=dt.dt.strftime('%Y-%m-%d')
    now=datetime.utcnow().isoformat(timespec='seconds')+'Z'
    if 'record_id' not in t.columns:
        t['record_id']=[new_record_id(n, d) for n,d in zip(t.get('Ime i prezime',''), t['date_iso'])]
    if 'created_at' not in t.columns: t['created_at']=now
    if 'updated_at' not in t.columns: t['updated_at']=now
    if 'version' not in t.columns: t['version']=1
    if 'source' not in t.columns: t['source']=source
    return t

def dedupe_last_then_sort_desc(df: pd.DataFrame) -> pd.DataFrame:
    t = with_parsed_date(normalize_columns(df)).copy()
    if 'updated_at' in t.columns:
        t['_ts']=pd.to_datetime(t['updated_at'], errors='coerce')
        t=t.sort_values(['Ime i prezime','date_iso','_ts'], ascending=[True,True,True], kind='stable')
        t=t.drop_duplicates(subset=['Ime i prezime','date_iso'], keep='last')
        t=t.drop(columns=['_ts'], errors='ignore')
    else:
        t=t.drop_duplicates(subset=['Ime i prezime','date_iso'], keep='last')
    t=t.sort_values(['Datum_dt','Ime i prezime'], ascending=[False, True], na_position='last')
    return t

def is_remote_value(s)->bool:
    if not isinstance(s,str): return False
    l=s.lower()
    return ("remote" in l) or ("rad od ku" in l) or ("work from home" in l) or ("home office" in l)

def validate_tracker_schema(df: pd.DataFrame) -> list[str]:
    t=normalize_columns(df)
    required=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year','date_iso','record_id','created_at','updated_at','source','version']
    missing=[c for c in required if c not in t.columns]
    problems=[]
    if missing: problems.append(f"Nedostaju kolone: {missing}")
    dup=t.duplicated(subset=['Ime i prezime','date_iso'], keep=False)
    if dup.any():
        k=t.loc[dup, ['Ime i prezime','date_iso']].drop_duplicates().values.tolist()
        problems.append(f"Duplikati po (Ime i prezime, date_iso): {k[:5]}{'...' if len(k)>5 else ''}")
    bad_date=t['date_iso'].isna() | (t['date_iso'].astype(str)=='NaT')
    if bad_date.any(): problems.append("Nevaljani date_iso zapisi postoje.")
    return problems
