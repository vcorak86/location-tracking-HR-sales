from pathlib import Path
from datetime import date, datetime, timedelta
import base64, io, requests, re, time, random, unicodedata
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

# utils_tracker mora postojati (kao i prije)
from utils_tracker import (
    normalize_columns,
    with_parsed_date,
    dedupe_last_then_sort_desc,
    is_remote_value,
    apply_canonical_fields,
    validate_tracker_schema,
)

# ---- Build metadata ----
BUILD_VERSION = "v11.0"
BUILD_TIMESTAMP = datetime.utcnow().isoformat(timespec="seconds") + "Z"

# ----- Optional telemetry -----
try:
    import sentry_sdk
except Exception:
    sentry_sdk = None

# ----- Paths / files -----
EMP_FILE = "data/Popis_djelatnika_HR_Sales.csv"
# LOC_FILE removed
LOC_NORM_FILE = "data/Locations_normalized.csv"   # jedini izvor lokacija
HOL_FILE = "data/CroatianHolidays.csv"
GH_TRACKER_PATH_DEFAULT = "data/Tracker.csv"
PENDING_FILE = Path("data/Tracker.pending.csv")
LOCAL_FALLBACK_LOG = Path("data/Tracker.local.csv")
DEFAULT_GH_SEP = ";"
HR_DAYS = ["Ponedjeljak","Utorak","Srijeda","Četvrtak","Petak"]
MONTHS_HR = ["Siječanj","Veljača","Ožujak","Travanj","Svibanj","Lipanj","Srpanj","Kolovoz","Rujan","Listopad","Studeni","Prosinac"]

st.set_page_config(page_title="Praćenje lokacije rada", page_icon="🗺️", layout="wide")

# Telemetry init
if sentry_sdk and "SENTRY" in st.secrets and st.secrets["SENTRY"].get("dsn"):
    sentry_sdk.init(
        dsn=st.secrets["SENTRY"]["dsn"],
        traces_sample_rate=float(st.secrets["SENTRY"].get("traces_sample_rate", 0.1))
    )

# ---------- Styles ----------
st.markdown('''
<style>
.hday-cell { background:#fff7d6; padding:10px 12px; border-top:1px solid #f1c40f55; border-bottom:1px solid #f1c40f55; }
.hday-left { border-left:1px solid #f1c40f55; border-top-left-radius:10px; border-bottom-left-radius:10px; }
.hday-right{ border-right:1px solid #f1c40f55; border-top-right-radius:10px; border-bottom-right-radius:10px; }
.hday-cell:hover { background:#fff1bd; box-shadow:0 0 0 2px #f1c40f3a inset; transition: all .15s ease; }
.label-strong { font-weight:600; }
.pin-input label { display:none !important; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; background:#e2e8f0; color:#0f172a; font-size:12px; font-weight:600;}
.badge-dot { height:8px; width:8px; border-radius:50%; display:inline-block; background:#10b981; margin-right:6px; vertical-align:middle;}
.small { font-size:12px; color:#475569; }
</style>
''', unsafe_allow_html=True)

# ---------- Encoding detection ----------
def detect_encoding(path: str):
    try:
        from charset_normalizer import from_bytes
        raw = Path(path).read_bytes()
        best = from_bytes(raw).best()
        if best and best.encoding:
            return best.encoding
    except Exception:
        pass
    return None

def read_csv_smart(path:str, force_sep=None, seps=(",", ";", "\\t", "|"), encs=("utf-8","utf-8-sig","cp1250","latin1")):
    enc_detected = detect_encoding(path)
    tried=set()
    enc_order = [enc_detected] + [e for e in encs if e and (not enc_detected or e.lower()!=enc_detected.lower())]
    sep_list = [force_sep] if force_sep else list(seps)
    last_err=None
    for enc in enc_order:
        if not enc: continue
        for sep in sep_list:
            key=(enc, sep)
            if key in tried: continue
            tried.add(key)
            try:
                return pd.read_csv(path, sep=sep, encoding=enc, engine="python")
            except Exception as e:
                last_err=e
                continue
    try:
        return pd.read_csv(path, sep=None, engine="python", encoding=enc_detected or "utf-8")
    except Exception as e2:
        last_err = last_err or e2
    if last_err: raise last_err
    raise RuntimeError(f"Ne mogu učitati CSV: {path}")

# ---------- GitHub helpers ----------
def gh_enabled(): return "GITHUB" in st.secrets and all(k in st.secrets["GITHUB"] for k in ["token","repo"])
def _gh_headers(extra=None):
    h={"Authorization": f"Bearer {st.secrets['GITHUB']['token']}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}
    if extra: h.update(extra)
    return h
def _sanitize_repo(repo:str)->str: return repo.strip().strip("/")
def _gh_config():
    s=st.secrets["GITHUB"]
    return {"repo":_sanitize_repo(s["repo"]), "branch":s.get("branch","main"), "path":s.get("path", GH_TRACKER_PATH_DEFAULT),
            "committer_name":s.get("committer_name",None), "committer_email":s.get("committer_email",None),
            "csv_sep":s.get("csv_sep", DEFAULT_GH_SEP)}
def gh_get_file(repo,path,branch, etag=None):
    headers=_gh_headers({"If-None-Match": etag} if etag else None)
    url=f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r=requests.get(url, headers=headers, timeout=30)
    return r
def gh_put_file(repo,path,branch,content_bytes,message,sha=None,committer_name=None,committer_email=None):
    data={"message":message,"content":base64.b64encode(content_bytes).decode("utf-8"),"branch":branch}
    if sha: data["sha"]=sha
    if committer_name and committer_email: data["committer"]={"name":committer_name,"email":committer_email}
    url=f"https://api.github.com/repos/{repo}/contents/{path}"
    r=requests.put(url, headers=_gh_headers(), json=data, timeout=60)
    return r

# ---------- Loaders ----------
@st.cache_data(show_spinner=False)
def load_employees(path:str)->pd.DataFrame:
    df=read_csv_smart(path, force_sep=';')
    if len(df.columns)==1 and ';' in str(df.columns[0]):
        for enc in ("utf-8","utf-8-sig","cp1250","latin1"):
            try:
                df = pd.read_csv(path, sep=';', engine="python", encoding=enc)
                break
            except Exception:
                continue
    import unicodedata
    def norm(s:str)->str:
        s=unicodedata.normalize("NFKD", str(s))
        s="".join(ch for ch in s if not unicodedata.combining(ch))
        s=s.lower()
        s=re.sub(r"[^a-z0-9]+","", s)
        return s
    norm_map={c: norm(c) for c in df.columns}
    inv={}
    for orig, n in norm_map.items(): inv.setdefault(n, orig)
    def pick(*cands):
        for cand in cands:
            n=norm(cand)
            if n in inv: return inv[n]
        return None
    col_name = pick("Name","Ime i prezime","ImeIPrezime","Zaposlenik","Employee")
    col_dept = pick("Department","Odjel","Odjeljenje","OrgUnit","Organizacijska jedinica")
    col_mail = pick("eMail","Email","E-mail","e-mail","mail","Kontakt e-mail","Kontakt email")
    col_mgr  = pick("Manager","Menadzer","Menadžer","Prvi nadređeni","Nadređeni","Line Manager")
    col_dir  = pick("Director","Direktor","Drugi nadređeni")

    missing=[]
    if not col_name: missing.append("Name / Ime i prezime")
    if not col_dept: missing.append("Department / Odjel")
    if not col_mail: missing.append("Email / eMail / E-mail")
    if missing:
        st.error("U CSV-u nedostaju obavezne kolone: " + ", ".join(missing) + f"\nNađene kolone: {list(df.columns)}")
        st.stop()

    base={col_name:"Name", col_dept:"Department", col_mail:"eMail"}
    out=df[list(base.keys())].rename(columns=base)
    out["Manager"]=df[col_mgr].astype(str) if col_mgr else ""
    out["Director"]=df[col_dir].astype(str) if col_dir else ""
    out["eMail_lc"]=out["eMail"].astype(str).str.strip().str.lower()
    return out[["Name","Department","eMail","Manager","Director","eMail_lc"]]

@st.cache_data(show_spinner=False)
def load_locations_norm(path:str)->pd.DataFrame:
    if not Path(path).exists(): 
        return pd.DataFrame(columns=['location_id','name','type','aliases'])
    df=read_csv_smart(path, force_sep=";")
    # header fix if needed
    cols_low=[c.lower() for c in df.columns]
    if all(str(c).lower().startswith("column") for c in df.columns) and len(df)>0:
        new_header=[str(x).strip() for x in df.iloc[0].tolist()]
        df=df.iloc[1:].reset_index(drop=True)
        df.columns=new_header
    df=df.rename(columns={c:str(c).strip().lower() for c in df.columns})
    for c in ['location_id','name','type','aliases']:
        if c not in df.columns: df[c]=''
    df['aliases']=df['aliases'].fillna('').astype(str)
    df['type']=df['type'].fillna('').astype(str).str.upper()
    df['name']=df['name'].astype(str).str.strip()
    df['location_id']=df['location_id'].astype(str).str.strip()
    df=df[(df['name']!='') & (df['location_id']!='')]
    return df[['location_id','name','type','aliases']].drop_duplicates('location_id', keep='last')

@st.cache_data(show_spinner=False)
def load_holidays_csv(path:str):
    df=read_csv_smart(path, force_sep=';')
    cols_lower={str(c).lower():c for c in df.columns}
    date_col=next((cols_lower[k] for k in cols_lower if 'datum'in k or 'date'in k), df.columns[0])
    name_col=next((cols_lower[k] for k in cols_lower if any(x in k for x in ['praznik','holiday','naziv','name'])), df.columns[1] if len(df.columns)>1 else df.columns[0])
    df['_date']=pd.to_datetime(df[date_col].astype(str).str.strip().str.rstrip('.'), dayfirst=True, errors='coerce').dt.date
    df['_name']=df[name_col].astype(str).str.strip()
    return {r['_date']:r['_name'] for _,r in df.dropna(subset=['_date']).iterrows()}

employees = load_employees(EMP_FILE)
LOC_NORM   = load_locations_norm(LOC_NORM_FILE)
HOLIDAYS   = load_holidays_csv(HOL_FILE)

# ---------- Location catalog (ONLY from Locations_normalized.csv) ----------
def _norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def build_location_catalog(norm_df: pd.DataFrame):
    """Returns: (sorted_canonical_names, alias_to_canonical, canonical_to_type, canonical_to_id)"""
    if norm_df is None or norm_df.empty:
        return [], {}, {}, {}
    names = []
    alias_map = {}
    type_map = {}
    id_map   = {}

    for _, r in norm_df.iterrows():
        canon = str(r.get("name","")).strip()
        if not canon:
            continue
        typ = str(r.get("type","")).strip().upper()
        lid = str(r.get("location_id","")).strip()
        if canon not in names:
            names.append(canon)
        type_map[_norm_key(canon)] = typ
        id_map[_norm_key(canon)]   = lid

        # self-alias + aliases
        alias_map[_norm_key(canon)] = canon
        ali = str(r.get("aliases","")).strip()
        if ali:
            for a in ali.split("|"):
                a = a.strip()
                if a:
                    alias_map[_norm_key(a)] = canon

    names = sorted(list(dict.fromkeys(names)))
    return names, alias_map, type_map, id_map

LOC_OPTIONS, LOC_ALIAS_MAP, LOC_TYPE_MAP, LOC_ID_MAP = build_location_catalog(LOC_NORM)

def map_to_canonical(user_value: str) -> str:
    """Return canonical name or original (if no mapping). Block 'Neradni dan'."""
    if not user_value:
        return ""
    s = str(user_value).strip()
    if re.search(r"(?i)^neradni\s*dan$", s):
        return "__BLOCKED__"
    key = _norm_key(s)
    return LOC_ALIAS_MAP.get(key, s)

def is_remote_by_catalog(loc_value: str) -> bool:
    """Remote classification via normalized 'type'; fallback to heuristic."""
    if not loc_value:
        return False
    canon = map_to_canonical(loc_value)
    if canon == "__BLOCKED__":
        return False
    typ = LOC_TYPE_MAP.get(_norm_key(canon), "")
    if typ in {"REMOTE"}:   # <-- ako koristiš WFH/HOME i sl., dodaj u set
        return True
    # fallback
    return is_remote_value(str(canon))

# ---------- CSV parse helper for remote Tracker fetch ----------
def parse_csv_bytes(b:bytes, preferred_sep=";"):
    for sep in [preferred_sep]+[s for s in [",",";","\\t","|"] if s!=preferred_sep]:
        try:
            df=pd.read_csv(io.BytesIO(b), sep=sep, engine="python")
            if df.shape[1]>=3: return df, sep
        except Exception: pass
    df=pd.read_csv(io.BytesIO(b), sep=None, engine="python"); return df, None

# ---------- Tracker loader ----------
def load_tracker_and_meta():
    cfg=_gh_config() if gh_enabled() else None
    etag_prev=st.session_state.get("tracker_etag")
    if gh_enabled():
        r=gh_get_file(cfg['repo'], cfg['path'], cfg['branch'], etag=etag_prev)
        if r.status_code==304:
            df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame()
            sha=None; etag=r.headers.get('ETag', etag_prev)
        elif r.status_code==200:
            j=r.json(); content=base64.b64decode(j['content']); sha=j.get('sha'); etag=r.headers.get('ETag')
            try:
                df=pd.read_parquet(io.BytesIO(content))
            except Exception:
                df,_sep=parse_csv_bytes(content, preferred_sep=cfg['csv_sep'])
            df=dedupe_last_then_sort_desc(apply_canonical_fields(df, source='gh'))
            try: LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True); df.to_csv(LOCAL_FALLBACK_LOG, index=False)
            except Exception: pass
        elif r.status_code==404:
            df=pd.DataFrame()
            sha=None; etag=None
        else:
            st.error(f"GitHub GET error: {r.status_code}"); st.code(r.text)
            df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame()
            sha=None; etag=None
        st.session_state['tracker_sha']=sha
        st.session_state['tracker_etag']=etag
    else:
        df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame()
        sha=None; etag=None
    return df, sha, etag, cfg

# ---------- “Last completed week” helper ----------
def monday_of_week(d:date)->date: return (pd.Timestamp(d)-pd.Timedelta(days=d.weekday())).date()
def last_completed_week_end(today: date) -> date:
    """End (Sunday) of the last fully completed ISO week."""
    start_this = monday_of_week(today)                 # Monday of current week
    end_prev   = (pd.Timestamp(start_this) - pd.Timedelta(days=1)).date()  # Sunday before
    return end_prev

def iso_week(dt:date)->int: return pd.Timestamp(dt).isocalendar().week
def week_bounds(monday:date):
    end=(pd.Timestamp(monday)+pd.Timedelta(days=6)).date(); return monday, end

# ---------- Save helper (adds location_id & location_name) ----------
def save_tracker_rows(new_rows:pd.DataFrame):
    # map canonical + IDs
    rows = new_rows.copy()
    rows['location_name'] = ''
    rows['location_id']   = ''
    for idx, r in rows.iterrows():
        raw = str(r.get('Lokacija','')).strip()
        canon = map_to_canonical(raw)
        if canon == "__BLOCKED__":
            rows.at[idx,'Lokacija'] = ''      # blocked
            rows.at[idx,'location_name'] = ''
            rows.at[idx,'location_id']   = ''
        else:
            rows.at[idx,'Lokacija'] = canon   # overwrite to canonical
            rows.at[idx,'location_name'] = canon
            rows.at[idx,'location_id']   = LOC_ID_MAP.get(_norm_key(canon), "")

    # proceed with existing merge/last-wins/PUT to GH
    prog = st.progress(0, text="Spremam zapise …")
    existing,_sha,_etag,_cfg = load_tracker_and_meta(); prog.progress(20, text="Spajam …")
    merged=pd.concat([existing, rows], ignore_index=True)
    merged=apply_canonical_fields(merged, source='app'); prog.progress(45, text="Normaliziram (DESC + last-wins) …")
    merged=dedupe_last_then_sort_desc(merged)

    # local write
    try:
        prog.progress(60, text="Lokalni zapis …")
        LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True); merged.to_csv(LOCAL_FALLBACK_LOG, index=False)
    except Exception: pass

    # push to GH
    if gh_enabled():
        cfg=_gh_config()
        pref=['Datum','Dan','Ime i prezime','Odjel','Lokacija',
              'Week','Month','Year','date_iso','record_id',
              'location_id','location_name',
              'created_at','updated_at','source','version']
        cols=[c for c in pref if c in merged.columns]+[c for c in merged.columns if c not in pref]
        out_csv = merged[cols].to_csv(index=False, sep=cfg['csv_sep']).encode('utf-8')
        r=gh_get_file(cfg['repo'], cfg['path'], cfg['branch'])
        sha=None
        if r.status_code==200:
            try: sha=r.json()['sha']
            except Exception: sha=None
        put=gh_put_file(cfg['repo'], cfg['path'], cfg['branch'], out_csv,
                        "Update Tracker.csv (DESC, last-wins, canonical + location_id/name) from Streamlit",
                        sha, cfg.get('committer_name'), cfg.get('committer_email'))
        if put.status_code not in (200,201):
            st.error(f"GitHub PUT error {put.status_code}")
            st.code(put.text)
    prog.progress(100, text="Spremanje završeno.")
    st.session_state["tracker_version"] = st.session_state.get("tracker_version", 0) + 1
    st.rerun()

# ---------- Title + header ----------
df_init, sha_init, etag_init, cfg = load_tracker_and_meta()
sha_short = (sha_init or "local")[:7] if sha_init else "local"
branch = (cfg['branch'] if cfg else "local")
path_remote = (cfg['path'] if cfg else "data/Tracker.csv")

c_title, c_right = st.columns([6,3])
with c_title:
    st.title("Dobrodošli u aplikaciju za praćenje lokacije rada!")
with c_right:
    if st.button("🔔 Provjeri nove zapise", help="Provjeri ima li novog commita u data/Tracker.csv"):
        st.session_state["tracker_version"] = st.session_state.get("tracker_version", 0) + 1
        st.toast("Provjeravam GitHub …", icon="🔔")
        st.rerun()
    st.markdown(f"<span class='badge'><span class='badge-dot'></span>{branch} · {path_remote} · @{sha_short}</span>", unsafe_allow_html=True)

# ---------- Email gate ----------
email=st.text_input("Unesite svoju eMail adresu").strip().lower()
if not email: st.stop()

# Base data
df_emp=load_employees(EMP_FILE)
row=df_emp[df_emp['eMail_lc']==email]
if row.empty: st.error("E-mail nije pronađen u popisu djelatnika."); st.stop()
person=row.iloc[0]; full_name=str(person['Name']); dept=str(person['Department'])
st.success(f"Pozdrav, **{full_name}** ({dept})!")

# ---------- Admin portal ----------
with st.expander("🛠️ Admin portal"):
    if 'admin_ok' not in st.session_state: st.session_state['admin_ok']=False
    if not st.session_state['admin_ok']:
        with st.form("admin_unlock", clear_on_submit=True):
            pin = st.text_input("Admin PIN", type="password", placeholder="PIN", label_visibility="collapsed", key="admin_pin")
            submitted = st.form_submit_button("🔓")
        if submitted:
            if pin == "1986":
                st.session_state['admin_ok'] = True
            else:
                st.error("Neispravan PIN.")
    else:
        colA, colB, colC = st.columns([5,2,1])
        with colA: st.success("Admin pristup odobren.")
        with colB:
            if st.button("🔒 Zaključaj", help="Zaključa admin portal"): st.session_state['admin_ok'] = False
        with colC:
            if st.button("🔁 Sync pending"): st.info("NIY (ako koristiš pending queue).")
        st.divider()
        if st.button('🧹 Očisti cache'):
            st.cache_data.clear()
            st.rerun()

        # Healthcheck (skraćeno)
        st.markdown("### Healthcheck")
        if gh_enabled():
            try:
                test_path="data/connection_check.txt"; payload=f"OK {datetime.utcnow().isoformat()}Z".encode("utf-8")
                rget=gh_get_file(cfg['repo'], test_path, cfg['branch'])
                sha = rget.json().get('sha') if rget.status_code==200 else None
                rput=gh_put_file(cfg['repo'], test_path, cfg['branch'], payload, "Connection check from Streamlit",
                                 sha, cfg.get('committer_name'), cfg.get('committer_email'))
                st.write(f"GET:{rget.status_code} PUT:{rput.status_code}")
            except Exception as e:
                st.warning(f"Healthcheck problem: {e}")
        else:
            st.warning("GITHUB secrets nisu postavljeni.")

        # -------------- ADMIN ANALITIKA (do zadnjeg završenog tjedna) --------------
        all_data_raw = df_init.copy()
        all_data = with_parsed_date(normalize_columns(all_data_raw))
        # enrich manager/department via employees
        try:
            name2mgr=df_emp.set_index('Name')[['Manager','Department']]
            all_data=all_data.merge(name2mgr, left_on='Ime i prezime', right_index=True, how='left')
        except Exception:
            pass

        # cutoff: last completed week end (Sunday)
        cutoff = last_completed_week_end(date.today())
        cutoff_ts = pd.Timestamp(cutoff)
        all_data = all_data[all_data['Datum_dt'] <= cutoff_ts]

        # sliceri
        st.markdown("### Analitika lokacija (zaključno sa zadnjim dovršenim tjednom)")
        st.caption(f"Analitika uključuje zapise **do zaključno s tjednom {iso_week(cutoff)}** (do {cutoff.strftime('%d.%m.%Y.')}), "
                   f"**ne uključuje tekući tjedan {iso_week(date.today())}**.")

        c_year, c_quarter, c_month = st.columns([1,1,2])
        years=sorted([int(y) for y in all_data['Godina'].dropna().unique() if str(y).isdigit()], reverse=True)
        with c_year: sel_years=st.multiselect("Godina", options=years, default=years[:1] if years else [])
        with c_quarter: sel_quarters=st.multiselect("Kvartal", options=[1,2,3,4], default=[])
        with c_month:
            months=list(range(1,13)); month_labels={i: MONTHS_HR[i-1] for i in months}
            sel_months=st.multiselect("Mjesec", options=months, format_func=lambda x: month_labels[x], default=[])

        c_filters=st.columns([2,2,2])
        with c_filters[0]:
            depts=sorted([d for d in all_data.get('Odjel', pd.Series([])).dropna().unique()])
            sel_depts=st.multiselect("Odjel", options=depts, default=[])
        with c_filters[1]:
            mgrs=sorted([m for m in all_data.get('Manager', pd.Series([])).dropna().unique()])
            sel_mgrs=st.multiselect("Manager", options=mgrs, default=[])
        with c_filters[2]:
            people=sorted([p for p in all_data.get('Ime i prezime', pd.Series([])).dropna().unique()])
            sel_people=st.multiselect("Osoba", options=people, default=[])

        df = all_data.copy()
        if sel_years: df=df[df['Godina'].astype('Int64').isin(sel_years)]
        if sel_quarters: df=df[df['Kvartal'].isin(sel_quarters)]
        if sel_months: df=df[df['Mjesec'].isin(sel_months)]
        if sel_depts: df=df[df['Odjel'].isin(sel_depts)]
        if sel_mgrs: df=df[df['Manager'].isin(sel_mgrs)]
        if sel_people: df=df[df['Ime i prezime'].isin(sel_people)]

        chart_type=st.radio("Prikaz", ["Pie","Column","Bar","Stacked by day"], horizontal=True, index=0)
        thresh = st.slider("Spajanje malih kategorija u 'Ostalo' (u %)", 0, 10, 3, 1)

        if not df.empty:
            # map to canonical (for safety) before counts
            df_plot=df.copy()
            df_plot['Lokacija'] = df_plot['Lokacija'].astype(str).map(map_to_canonical)
            counts=df_plot['Lokacija'].value_counts()
            total=int(counts.sum())

            # aggregate "small to Ostalo"
            if total>0 and thresh>0:
                keep=[k for k,v in counts.items() if (v/total*100)>=thresh]
                df_plot['Lokacija2']=df_plot['Lokacija'].apply(lambda x: x if x in keep else 'Ostalo')
                counts=df_plot['Lokacija2'].value_counts()
                total=int(counts.sum())

            if total>0:
                if chart_type=='Pie':
                    fig,ax=plt.subplots(figsize=(5.2,5.2)); wedges,_=ax.pie(counts.values, startangle=90); ax.axis('equal')
                    labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
                    ax.legend(wedges, labels, loc='center left', bbox_to_anchor=(1,0.5)); st.pyplot(fig)
                elif chart_type=='Column':
                    fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.bar(counts.index.astype(str), counts.values)
                    ax.set_xlabel('Lokacija'); ax.set_ylabel('Broj'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)
                elif chart_type=='Bar':
                    fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.barh(counts.index.astype(str), counts.values)
                    ax.set_xlabel('Broj'); ax.set_ylabel('Lokacija'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)
                elif chart_type=='Stacked by day':
                    grp=df_plot.groupby(['Dan','Lokacija']).size().unstack(fill_value=0)
                    fig,ax=plt.subplots(figsize=(7,4)); bottom=None
                    for col in grp.columns:
                        vals=grp[col].values
                        if bottom is None:
                            ax.bar(grp.index.astype(str), vals, label=col); bottom=vals
                        else:
                            ax.bar(grp.index.astype(str), vals, bottom=bottom, label=col); bottom=[b+v for b,v in zip(bottom,vals)]
                    ax.legend(); ax.set_xlabel('Dan'); ax.set_ylabel('Broj'); st.pyplot(fig)

                # KPI po osobi s REMOTE tipom iz normalized
                st.markdown("#### KPI po osobi (Ured / Remote / Ostalo) — do zadnjeg završenog tjedna")
                res=[]
                for person_i, g in df_plot.groupby('Ime i prezime'):
                    office = 0
                    remote = 0
                    for x in g['Lokacija'].astype(str):
                        canon = map_to_canonical(x)
                        typ = LOC_TYPE_MAP.get(_norm_key(canon), "")
                        if typ == "URED":
                            office += 1
                        elif typ in {"REMOTE"}:
                            remote += 1
                        else:
                            # fallback heuristika
                            if is_remote_value(str(canon)):
                                remote += 1
                    total_i = int(len(g))
                    other = int(total_i - office - remote)
                    pct=round(remote/total_i*100,1) if total_i>0 else 0.0
                    res.append({"Ime i prezime":person_i,"Ured":office,"Remote":remote,"Ostalo":other,"Ukupno":total_i,"% Remote":pct})
                kpi=pd.DataFrame(res).sort_values(["Remote","% Remote"], ascending=[False,False])
                st.dataframe(kpi, width='stretch', hide_index=True)

                # tablica po lokaciji
                st.markdown("#### Tablica (apsolutni brojevi i postotci)")
                table=pd.DataFrame({"Lokacija":counts.index,"Broj":counts.values,"Postotak":(counts.values/total*100).round(1)})
                st.dataframe(table, width='stretch', hide_index=True)
            else:
                st.info("Nema podataka za prikaz.")
        else:
            st.info("Nema podataka nakon primijenjenih filtera.")

# ---------- Weekly entry (unos) ----------
def weeks_forward_until_year_end(ref:date)->int:
    year_end=date(ref.year,12,31)
    start_monday=monday_of_week(ref); end_monday=monday_of_week(year_end)
    delta_days=(pd.Timestamp(end_monday)-pd.Timestamp(start_monday)).days
    return max(0, int(delta_days//7))

today=date.today()
MAX_WEEKS_FWD=weeks_forward_until_year_end(today)
if 'week_offset' not in st.session_state: st.session_state.week_offset=1

cols_nav = st.columns([1,1,1,1,1,1])
with cols_nav[0]:
    if st.button("⬅️ Prethodni tjedan", disabled=st.session_state.week_offset<=-2): st.session_state.week_offset-=1
with cols_nav[1]:
    if st.button("📅 Ovaj tjedan"): st.session_state.week_offset=0
with cols_nav[2]:
    if st.button("⏭️ Sljedeći tjedan"): st.session_state.week_offset=1
with cols_nav[3]:
    if st.button("➡️ Sljedeći ➕", disabled=st.session_state.week_offset>=MAX_WEEKS_FWD): st.session_state.week_offset+=1
with cols_nav[4]:
    copy_last = st.button("📋 Kopiraj prošli tjedan")
with cols_nav[5]:
    reset_week = st.button("🧹 Resetiraj tjedan")

week_monday=(pd.Timestamp(today)-pd.Timedelta(days=today.weekday())+pd.Timedelta(weeks=st.session_state.week_offset)).date()
week_start, week_end = week_bounds(week_monday); week_num=iso_week(week_monday)
week_year = pd.Timestamp(week_monday).isocalendar().year
st.subheader(f"Tjedan {week_num} ({week_year}) ({week_start.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

# Prefill iz Trackera
tracker_all = df_init
prefill={}
if not tracker_all.empty:
    t=with_parsed_date(normalize_columns(tracker_all.copy()))
    mask=(t['Ime i prezime']==full_name) & (t['Datum_dt']>=pd.Timestamp(week_start)) & (t['Datum_dt']<=pd.Timestamp(week_end))
    for _,r in t[mask].iterrows(): prefill[r['Datum_dt'].date()]=str(r['Lokacija'])

if copy_last and not tracker_all.empty:
    t=with_parsed_date(normalize_columns(tracker_all.copy()))
    prev_monday=(pd.Timestamp(week_start)-pd.Timedelta(weeks=1)).date()
    pmask=(t['Ime i prezime']==full_name) & (t['Datum_dt']>=pd.Timestamp(prev_monday)) & (t['Datum_dt']<=pd.Timestamp(prev_monday)+pd.Timedelta(days=6))
    for _,r in t[pmask].iterrows(): prefill[r['Datum_dt'].date()]=str(r['Lokacija'])
if reset_week: prefill={}

admin_override = st.session_state.get('admin_ok', False)
locked = (week_end < today) and (not admin_override)
if locked: st.info("Ovaj tjedan je zaključan za izmjene (admin može override).")

with st.form("unos_tjedan"):
    st.write("**Datum, Dan, Lokacija** — Neradni dani su automatski označeni i nisu promjenjivi.")
    week_rows=[]; remote_count=0; any_empty=False; other_notes={}
    for i in range(5):
        d=(pd.Timestamp(week_start)+pd.Timedelta(days=i)).date()
        day_name=HR_DAYS[i]; hol=HOLIDAYS.get(d)
        c1,c2,c3=st.columns([2,2,3])
        if hol:
            with c1: st.markdown(f"<div class='hday-cell hday-left'><span class='label-strong'>Datum:</span> {pd.Timestamp(d).strftime('%d.%m.%Y.')}</div>", unsafe_allow_html=True)
            with c2: st.markdown(f"<div class='hday-cell'><span class='label-strong'>Dan:</span> {day_name}</div>", unsafe_allow_html=True)
            with c3: st.markdown(f"<div class='hday-cell hday-right'><span class='label-strong'>Lokacija:</span> {hol}</div>", unsafe_allow_html=True)
            val=hol
        else:
            default=prefill.get(d,"")
            with c1: st.markdown(f"**Datum:** {pd.Timestamp(d).strftime('%d.%m.%Y.')}")
            with c2: st.markdown(f"**Dan:** {day_name}")
            with c3:
                if locked:
                    st.text_input("Lokacija (zaključano)", value=default, disabled=True, key=f"lock_{d.isoformat()}")
                    val=default
                else:
                    sel=st.selectbox("Lokacija", ["(odaberi)"]+LOC_OPTIONS+["(drugo)"], index=0, key=f"sel_{d.isoformat()}")
                    if sel=="(drugo)":
                        raw=st.text_input("Ručni unos lokacije", value=default if default not in LOC_OPTIONS else "", key=f"free_{d.isoformat()}").strip()
                        if raw:
                            canonical = map_to_canonical(raw)
                            if canonical == "__BLOCKED__":
                                st.warning("Vrijednost 'Neradni dan' nije dopuštena za unos.")
                                val=""
                            else:
                                val=canonical
                        else:
                            val=""
                        note=st.text_input("Napomena (obavezno za 'drugo')", key=f"note_{d.isoformat()}").strip()
                        other_notes[d]=note
                    elif sel!="(odaberi)":
                        val=sel
                    else:
                        val=default
        if not hol and is_remote_by_catalog(val): remote_count+=1
        if not hol and not locked and not val: any_empty=True
        week_rows.append({
            "Datum":pd.Timestamp(d).strftime("%d.%m.%Y."),"Dan":day_name,"Ime i prezime":full_name,"Odjel":dept,"Lokacija":val,
            "Week":pd.Timestamp(d).isocalendar().week,"Month":d.month,"Year":d.year
        })

    st.markdown("##### Tjedni sažetak")
    st.dataframe(pd.DataFrame(week_rows)[["Datum","Dan","Lokacija"]], width='stretch', hide_index=True)
    if remote_count>1:
        st.warning('Prema internom dogovoru u odjelu Prodaje i marketinga, tjedno je moguće koristiti "Rad od kuće" jedan radni dan.')

    if st.form_submit_button("💾 Spremi tjedne unose"):
        if locked: st.error("Tjedan je zaključan; izmjena nije dopuštena."); st.stop()
        if any_empty: st.error("Molimo unesite lokaciju za svaki radni dan ili ostavite sva polja prazna."); st.stop()

        to_save=[r for r in week_rows if r["Lokacija"]]
        if not to_save: st.info("Nema unosa za spremanje.")
        else:
            df_save=pd.DataFrame(to_save)
            # dodatne napomene za free-text izvan normalized
            if other_notes:
                df_save['Napomena']=''
                for idx,r in df_save.iterrows():
                    dstr=r['Datum']; ddate=pd.to_datetime(dstr, dayfirst=True).date()
                    canon = map_to_canonical(r['Lokacija'])
                    if ddate in other_notes and canon and _norm_key(canon) not in LOC_ID_MAP:
                        df_save.at[idx,'Napomena']=other_notes[ddate]
            save_tracker_rows(df_save)

# ---------- Personal analytics (THIS YEAR, UP TO LAST COMPLETED WEEK) ----------
st.markdown("---"); st.subheader("Udio lokacija u tekućoj godini (do zadnjeg dovršenog tjedna)")
cutoff = last_completed_week_end(date.today())
st.caption(f"Analitika uključuje zapise **do zaključno s tjednom {iso_week(cutoff)}** "
           f"(do {cutoff.strftime('%d.%m.%Y.')}), ne uključuje tekući tjedan {iso_week(date.today())}.")

with st.spinner("Računam osobnu analitiku …"):
    tracker_df = df_init
    if not tracker_df.empty:
        t=with_parsed_date(normalize_columns(tracker_df))
        mine=t[(t["Ime i prezime"]==full_name) & (t["Godina"]==date.today().year) & (t["Datum_dt"]<=pd.Timestamp(cutoff))]
        if not mine.empty:
            # map to canonical for stable labels
            counts=mine["Lokacija"].astype(str).map(map_to_canonical).value_counts()
            total=int(counts.sum())
            if total>0:
                fig,ax=plt.subplots(figsize=(5.2,5.2)); wedges,_=ax.pie(counts.values, startangle=90); ax.axis("equal")
                labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
                ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1,0.5)); st.pyplot(fig)
            else:
                st.info("Nema spremljenih unosa za prikaz.")
        else: st.info("Nema spremljenih unosa za tekuću godinu u dovršenim tjednima.")
    else: st.info("Još nema podataka u Tracker.csv.")

# ---------- Past records ----------
st.markdown("---"); st.subheader("📜 Vaši prijašnji zapisi")
with st.spinner("Učitavam prijašnje zapise …"):
    tracker_all=df_init
    if not tracker_all.empty:
        mine=tracker_all[tracker_all["Ime i prezime"]==full_name].copy()
        try: mine["_d"]=pd.to_datetime(mine.get("date_iso", mine["Datum"]), errors="coerce"); mine=mine.sort_values("_d", ascending=False).drop(columns="_d")
        except Exception: pass
        show=[c for c in ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year","location_id","location_name"] if c in mine.columns]
        if show: st.dataframe(mine[show], width='stretch', hide_index=True)
        else: st.info("Nema podataka za prikaz.")
    else: st.info("Tracker.csv je prazan ili nedostupan.")

st.caption("Centralni dnevnik je **data/Tracker.csv** (DESC + last-wins, canonical, + location_id/name). Lokalni keš: data/Tracker.local.csv.")
