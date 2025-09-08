
from pathlib import Path
from datetime import date, datetime
import base64, io, requests, re, time, random
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from utils_tracker import normalize_columns, with_parsed_date, dedupe_last_then_sort_desc, is_remote_value, apply_canonical_fields, validate_tracker_schema
from scripts.make_pdf import make_simple_pdf

# Optional telemetry
try:
    import sentry_sdk
except Exception:
    sentry_sdk = None

EMP_FILE = "data/Popis_djelatnika_HR_Sales.csv"
LOC_FILE = "data/Locations.csv"
LOC_NORM_FILE = "data/Locations_normalized.csv"
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
    sentry_sdk.init(dsn=st.secrets["SENTRY"]["dsn"], traces_sample_rate=0.1)

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

def read_csv_smart(path:str, force_sep=None, seps=(",", ";", "\t", "|"), encs=("utf-8","utf-8-sig","cp1250","latin1")):
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

# ---------- GitHub helpers with ETag/backoff/pending ----------
def gh_enabled(): return "GITHUB" in st.secrets and all(k in st.secrets["GITHUB"] for k in ["token","repo"])
def _gh_headers(extra=None):
    h={"Authorization": f"Bearer {st.secrets['GITHUB']['token']}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
    if extra: h.update(extra)
    return h
def _sanitize_repo(repo:str)->str: return repo.strip().strip("/")
def _gh_config():
    s=st.secrets["GITHUB"]
    return {"repo":_sanitize_repo(s["repo"]), "branch":s.get("branch","main"), "path":s.get("path", GH_TRACKER_PATH_DEFAULT),
            "committer_name":s.get("committer_name",None), "committer_email":s.get("committer_email",None),
            "csv_sep":s.get("csv_sep", DEFAULT_GH_SEP)}
def gh_repo_info(repo:str):
    r=requests.get(f"https://api.github.com/repos/{repo}", headers=_gh_headers(), timeout=30)
    return r.status_code, r.text
def _backoff_sleep(i):
    time.sleep(min(5, (2**i) + random.random()))
def gh_get_file(repo,path,branch, etag=None):
    headers=_gh_headers({"If-None-Match": etag} if etag else None)
    url=f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    for i in range(3):
        r=requests.get(url, headers=headers, timeout=30)
        if r.status_code in (200,304,404): 
            return r
        if r.status_code in (429,500,502,503,504):
            _backoff_sleep(i)
            continue
        break
    return r
def gh_put_file(repo,path,branch,content_bytes,message,sha=None,committer_name=None,committer_email=None):
    data={"message":message,"content":base64.b64encode(content_bytes).decode("utf-8"),"branch":branch}
    if sha: data["sha"]=sha
    if committer_name and committer_email: data["committer"]={"name":committer_name,"email":committer_email}
    url=f"https://api.github.com/repos/{repo}/contents/{path}"
    for i in range(3):
        r=requests.put(url, headers=_gh_headers(), json=data, timeout=60)
        if r.status_code in (200,201): return r
        if r.status_code in (429,500,502,503,504):
            _backoff_sleep(i); continue
        break
    return r
def gh_rate_limit():
    r=requests.get("https://api.github.com/rate_limit", headers=_gh_headers(), timeout=20)
    return r.json() if r.status_code==200 else {}
def gh_scopes():
    r=requests.get("https://api.github.com/user", headers=_gh_headers(), timeout=20)
    return r.headers.get("X-OAuth-Scopes","")

# ---------- Loaders ----------
@st.cache_data(show_spinner=False)
def load_employees(path: str) -> pd.DataFrame:
    df = read_csv_smart(path)

    # 1) Normaliziraj nazive kolona (lower, makni razmake/crtice/točke/diakritike)
    import unicodedata, re

    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", str(s))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "", s)  # makni sve osim slova/brojeva
        return s

    norm_map = {c: norm(c) for c in df.columns}
    # obrnuti lookup: normalizirano -> originalni naziv
    inv = {}
    for orig, n in norm_map.items():
        inv.setdefault(n, orig)

    def pick(*candidates):
        """Vrati prvi postojeći originalni naziv kolone koji odgovara popisu kandidata (po normaliziranom ključu)."""
        for cand in candidates:
            n = norm(cand)
            if n in inv:
                return inv[n]
        return None

    # 2) Pronađi kolone po fleksibilnim aliasima
    col_name = pick("Name", "Ime i prezime", "ImeIPrezime", "Zaposlenik", "Employee")
    col_dept = pick("Department", "Odjel", "Odjeljenje", "OrgUnit", "Organizacijska jedinica")
    col_mail = pick("eMail", "Email", "E-mail", "e-mail", "mail", "Kontakt e-mail", "Kontakt email")

    col_mgr  = pick("Manager", "Menadzer", "Menadžer", "Prvi nadređeni", "Nadređeni", "Line Manager")
    col_dir  = pick("Director", "Direktor", "Drugi nadređeni")

    missing = []
    if not col_name: missing.append("Name / Ime i prezime")
    if not col_dept: missing.append("Department / Odjel")
    if not col_mail: missing.append("Email / eMail / E-mail")

    if missing:
        st.error(
            "U CSV-u nedostaju obavezne kolone: " + ", ".join(missing) +
            f"\nNađene kolone: {list(df.columns)}"
        )
        st.stop()

    # 3) Sastavi standardizirani DataFrame s očekivanim imenima kolona
    base_cols = {col_name: "Name", col_dept: "Department", col_mail: "eMail"}
    out = df[list(base_cols.keys())].rename(columns=base_cols)

    if col_mgr:
        out["Manager"] = df[col_mgr].astype(str)
    else:
        out["Manager"] = ""

    if col_dir:
        out["Director"] = df[col_dir].astype(str)
    else:
        out["Director"] = ""

    # 4) Dodatna pomoćna kolona (za spajanje po emailu)
    out["eMail_lc"] = out["eMail"].astype(str).str.strip().str.lower()

    return out[["Name", "Department", "eMail", "Manager", "Director", "eMail_lc"]]


@st.cache_data(show_spinner=False)
def load_locations(path:str)->list[str]:
    df=read_csv_smart(path)
    vals=[str(df[df.columns[0]].iloc[i]).strip() for i in range(len(df))]
    out=[]
    for v in vals:
        if not v: continue
        if v.lower()=='neradni dan': continue
        if v not in out: out.append(v)
    return out

@st.cache_data(show_spinner=False)
def load_locations_norm(path:str)->pd.DataFrame:
    if not Path(path).exists(): 
        return pd.DataFrame(columns=['location_id','name','type','aliases'])
    df=read_csv_smart(path)
    ren={c: re.sub(r"\s+"," ", str(c).strip()).lower() for c in df.columns}
    df=df.rename(columns=ren)
    need=['location_id','name','type','aliases']
    for c in need:
        if c not in df.columns: df[c]=''
    df['aliases']=df['aliases'].fillna('').astype(str)
    return df[need]

@st.cache_data(show_spinner=False)
def load_holidays_csv(path:str):
    df=read_csv_smart(path, force_sep=';')
    cols_lower={c.lower():c for c in df.columns}
    date_col=next((cols_lower[k] for k in cols_lower if 'datum'in k or 'date'in k), df.columns[0])
    name_col=next((cols_lower[k] for k in cols_lower if 'praznik'in k or 'holiday'in k or 'naziv'in k or 'name'in k), df.columns[1] if len(df.columns)>1 else df.columns[0])
    df['_date']=pd.to_datetime(df[date_col].astype(str).str.strip(), dayfirst=True, errors='coerce').dt.date
    df['_name']=df[name_col].astype(str).str.strip()
    return {r['_date']:r['_name'] for _,r in df.dropna(subset=['_date']).iterrows()}

employees=load_employees(EMP_FILE)
LOCATIONS=load_locations(LOC_FILE)
LOC_NORM=load_locations_norm(LOC_NORM_FILE)
HOLIDAYS=load_holidays_csv(HOL_FILE)

def parse_csv_bytes(b:bytes, preferred_sep=";"):
    for sep in [preferred_sep]+[s for s in [",",";","\t","|"] if s!=preferred_sep]:
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
            df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
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
            df=pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
            sha=None; etag=None
        else:
            st.error(f"GitHub GET error: {r.status_code}"); st.code(r.text)
            df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
            sha=None; etag=None
        st.session_state['tracker_sha']=sha
        st.session_state['tracker_etag']=etag
    else:
        df=pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
        sha=None; etag=None
    return df, sha, etag, cfg

def save_tracker_rows(new_rows:pd.DataFrame):
    prog = st.progress(0, text="Spremam zapise …")
    existing,_sha,_etag,_cfg = load_tracker_and_meta(); prog.progress(20, text="Spajam …")
    merged=pd.concat([existing, new_rows], ignore_index=True)
    merged=apply_canonical_fields(merged, source='app'); prog.progress(45, text="Normaliziram (DESC + last-wins) …")
    merged=dedupe_last_then_sort_desc(merged)

    try:
        prog.progress(60, text="Lokalni zapis …")
        LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True); merged.to_csv(LOCAL_FALLBACK_LOG, index=False)
    except Exception: pass

    if gh_enabled():
        cfg=_gh_config()
        pref=['Datum','Dan','Ime i prezime','Odjel','Lokacija','Week','Month','Year','date_iso','record_id','created_at','updated_at','source','version']
        cols=[c for c in pref if c in merged.columns]+[c for c in merged.columns if c not in pref]
        out_csv = merged[cols].to_csv(index=False, sep=cfg['csv_sep']).encode('utf-8')
        r=gh_get_file(cfg['repo'], cfg['path'], cfg['branch'])
        sha=None
        if r.status_code==200:
            try: sha=r.json()['sha']
            except Exception: sha=None
        elif r.status_code!=404:
            st.error(f"GET radi PUT-a nije uspio ({r.status_code}) — spremam u pending.")
            PENDING_FILE.parent.mkdir(parents=True, exist_ok=True); new_rows.to_csv(PENDING_FILE, mode='a', index=False, header=not PENDING_FILE.exists())
            prog.progress(100, text="Spremanje (pending)."); return

        put=gh_put_file(cfg['repo'], cfg['path'], cfg['branch'], out_csv, "Update Tracker.csv (DESC, last-wins, canonical) from Streamlit", sha,
                        cfg['committer_name'], cfg['committer_email'])
        if put.status_code in (200,201): prog.progress(100, text="GitHub ažuriran.")
        else:
            st.error(f"GitHub PUT error {put.status_code} — zapisujem u pending.")
            st.code(put.text)
            PENDING_FILE.parent.mkdir(parents=True, exist_ok=True); new_rows.to_csv(PENDING_FILE, mode='a', index=False, header=not PENDING_FILE.exists())
            prog.progress(100, text="Spremanje (pending).")
    else:
        prog.progress(100, text="Spremanje lokalno završeno.")

    st.session_state["tracker_version"] = st.session_state.get("tracker_version", 0) + 1
    st.experimental_rerun()

def try_sync_pending():
    if not PENDING_FILE.exists(): st.info("Nema pending zapisa."); return
    pend=pd.read_csv(PENDING_FILE, sep=None, engine='python')
    if pend.empty: st.info("Nema pending zapisa."); return
    save_tracker_rows(pend)
    try: PENDING_FILE.unlink()
    except Exception: pass

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
        st.experimental_rerun()
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
        with st.form("admin_unlock"):
            pin = st.text_input("", type="password", placeholder="PIN", label_visibility="collapsed")
            submitted = st.form_submit_button("🔓 Otključaj")
        if submitted and pin == "1986":
            st.session_state['admin_ok'] = True
        elif submitted and pin != "":
            st.error("Neispravan PIN.")
    else:
        colA, colB, colC = st.columns([5,2,1])
        with colA: st.success("Admin pristup odobren.")
        with colB:
            if st.button("🔒 Zaključaj", help="Zaključa admin portal"): st.session_state['admin_ok'] = False
        with colC:
            if st.button("🔁 Sync pending"): try_sync_pending()

        # Healthcheck
        st.markdown("### Healthcheck")
        if gh_enabled():
            def gh_rate_limit(): 
                r=requests.get("https://api.github.com/rate_limit", headers=_gh_headers(), timeout=20)
                return r.json() if r.status_code==200 else {}
            def gh_scopes():
                r=requests.get("https://api.github.com/user", headers=_gh_headers(), timeout=20)
                return r.headers.get("X-OAuth-Scopes","")
            rate=gh_rate_limit(); scopes=gh_scopes()
            st.write(f"**Branch:** `{branch}` · **Path:** `{path_remote}` · **SHA:** `{sha_short}`")
            core=rate.get('resources',{}).get('core',{})
            st.write(f"**Rate limit:** {core.get('remaining','?')}/{core.get('limit','?')} (reset {core.get('reset','?')})")
            st.write(f"**Token scopes:** {scopes}")
            with st.spinner("PUT test datoteke..."):
                test_path="data/connection_check.txt"; payload=f"OK {datetime.utcnow().isoformat()}Z".encode("utf-8")
                rget=gh_get_file(cfg['repo'], test_path, cfg['branch'])
                sha = rget.json().get('sha') if rget.status_code==200 else None
                rput=gh_put_file(cfg['repo'], test_path, cfg['branch'], payload, "Connection check from Streamlit", sha,
                                 cfg['committer_name'], cfg['committer_email'])
                st.write(f"GET:{rget.status_code} PUT:{rput.status_code}")
        else:
            st.warning("GITHUB secrets nisu postavljeni.")

        # Analitika s filtrima
        all_data = with_parsed_date(normalize_columns(df_init.copy()))
        try:
            name2mgr=df_emp.set_index('Name')[['Manager','Department']]
            all_data=all_data.merge(name2mgr, left_on='Ime i prezime', right_index=True, how='left')
        except Exception: pass

        st.markdown("---"); st.subheader("Analitika lokacija")
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

        df=all_data.copy()
        if sel_years: df=df[df['Godina'].isin(sel_years)]
        if sel_quarters: df=df[df['Kvartal'].isin(sel_quarters)]
        if sel_months: df=df[df['Mjesec'].isin(sel_months)]
        if sel_depts: df=df[df['Odjel'].isin(sel_depts)]
        if sel_mgrs: df=df[df['Manager'].isin(sel_mgrs)]
        if sel_people: df=df[df['Ime i prezime'].isin(sel_people)]

        chart_type=st.radio("Prikaz", ["Pie","Column","Bar","Stacked by day","Heatmap (tjedni x osobe)"], horizontal=True, index=0)
        thresh = st.slider("Spajanje malih kategorija u 'Ostalo' (u %)", 0, 10, 3, 1)
        loc_counts=df['Lokacija'].value_counts()
        total=int(loc_counts.sum()) if not loc_counts.empty else 0
        show_locs=[k for k,v in loc_counts.items() if total and (v/total*100)>=thresh]
        df_plot=df.copy()
        if total:
            df_plot['Lokacija2']=df['Lokacija'].apply(lambda x: x if x in show_locs else 'Ostalo')

        if not loc_counts.empty:
            if chart_type=='Pie':
                counts=df_plot['Lokacija2'].value_counts()
                fig,ax=plt.subplots(figsize=(5.2,5.2))
                wedges,_=ax.pie(counts.values, startangle=90); ax.axis('equal')
                labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
                ax.legend(wedges, labels, loc='center left', bbox_to_anchor=(1,0.5)); st.pyplot(fig)
            elif chart_type=='Column':
                counts=df_plot['Lokacija2'].value_counts()
                fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.bar(counts.index.astype(str), counts.values)
                ax.set_xlabel('Lokacija'); ax.set_ylabel('Broj'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)
            elif chart_type=='Bar':
                counts=df_plot['Lokacija2'].value_counts()
                fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.barh(counts.index.astype(str), counts.values)
                ax.set_xlabel('Broj'); ax.set_ylabel('Lokacija'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)
            elif chart_type=='Stacked by day':
                grp=df.groupby(['Dan','Lokacija']).size().unstack(fill_value=0)
                fig,ax=plt.subplots(figsize=(7,4)); bottom=None
                for col in grp.columns:
                    vals=grp[col].values
                    if bottom is None:
                        ax.bar(grp.index.astype(str), vals, label=col); bottom=vals
                    else:
                        ax.bar(grp.index.astype(str), vals, bottom=bottom, label=col); bottom=[b+v for b,v in zip(bottom,vals)]
                ax.legend(); ax.set_xlabel('Dan'); ax.set_ylabel('Broj'); st.pyplot(fig)
            elif chart_type=='Heatmap (tjedni x osobe)':
                try:
                    pivot=df.copy()
                    pivot['Tjedan']=pd.to_datetime(pivot['date_iso']).dt.isocalendar().week
                    pivot=pivot.pivot_table(index='Ime i prezime', columns='Tjedan', values='Lokacija', aggfunc='count', fill_value=0)
                    fig,ax=plt.subplots(figsize=(8,6))
                    im=ax.imshow(pivot.values, aspect='auto')
                    ax.set_yticks(range(len(pivot.index))); ax.set_yticklabels(pivot.index)
                    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns, rotation=90)
                    ax.set_title('Broj unosa po tjednu'); st.pyplot(fig)
                except Exception:
                    st.info("Nedovoljno podataka za heatmap.")
        else:
            st.info("Nema podataka za prikaz.")

        st.markdown("#### Tablica (apsolutni brojevi i postotci)")
        if total:
            table=pd.DataFrame({"Lokacija":loc_counts.index,"Broj":loc_counts.values,"Postotak":(loc_counts.values/total*100).round(1)})
            st.dataframe(table, width='stretch', hide_index=True)

            st.markdown("#### KPI po osobi (Ured / Remote / Ostalo) + mjesečni trend")
            office_names={"ured"}
            res=[]
            for person, g in df.groupby('Ime i prezime'):
                locs=g['Lokacija'].astype(str).str.lower()
                office=int(locs.isin(office_names).sum())
                remote=int(locs.apply(is_remote_value).sum())
                total_i=int(len(g))
                other=int(total_i - office - remote)
                pct=round(remote/total_i*100,1) if total_i>0 else 0.0
                res.append({"Ime i prezime":person,"Ured":office,"Remote":remote,"Ostalo":other,"Ukupno":total_i,"% Remote":pct})
            kpi=pd.DataFrame(res).sort_values(["Remote","% Remote"], ascending=[False,False])
            st.dataframe(kpi, width='stretch', hide_index=True)

            st.markdown("##### Trend (mjesečno, udio Remote)")
            monthly=df.assign(ym=pd.to_datetime(df['date_iso']).dt.to_period('M')).groupby(['ym']).apply(
                lambda g: (g['Lokacija'].astype(str).str.lower().apply(is_remote_value).sum()/len(g))*100 if len(g)>0 else 0).reset_index(name='pct_remote')
            if not monthly.empty:
                fig,ax=plt.subplots(figsize=(6.5,3.6)); ax.plot(monthly['ym'].astype(str), monthly['pct_remote'].values, marker='o')
                ax.set_ylabel('% Remote'); ax.set_xlabel('Mjesec'); st.pyplot(fig)

            colx, coly, colz = st.columns([1,1,2])
            with colx:
                st.download_button("⬇️ CSV (zapisi)", data=df.to_csv(index=False).encode("utf-8"), file_name="analytics_records.csv", mime="text/csv")
            with coly:
                st.download_button("⬇️ CSV (po lokaciji)", data=table.to_csv(index=False).encode("utf-8"), file_name="analytics_by_location.csv", mime="text/csv")
            with colz:
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="Records")
                    table.to_excel(writer, index=False, sheet_name="By location")
                    kpi.to_excel(writer, index=False, sheet_name="KPI per person")
                buf.seek(0)
                st.download_button("⬇️ Excel (zapisi + lokacije + KPI)", data=buf.read(), file_name="analytics_export.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            st.markdown("##### PDF izvještaj (kratki sažetak)")
            try:
                fig,ax=plt.subplots(figsize=(4,3)); ax.pie(loc_counts.values, startangle=90); ax.axis('equal'); 
                png=io.BytesIO(); plt.savefig(png, format='png'); plt.close(fig); png.seek(0)
                table_rows=[["Lokacija","Broj","%"]]+[[str(a),int(b),f"{int(b)/total*100:.1f}%"] for a,b in zip(loc_counts.index, loc_counts.values)]
                pdf=make_simple_pdf("Analitika lokacija (sažetak)", table_rows, png.read())
                st.download_button("⬇️ PDF izvještaj", data=pdf, file_name="analytics_summary.pdf", mime="application/pdf")
            except Exception:
                pass

        st.markdown("---"); st.subheader("Ispravci zapisa")
        with st.form("admin_fix"):
            p_sel = st.selectbox("Osoba", sorted(all_data['Ime i prezime'].dropna().unique()))
            d_sel = st.date_input("Datum", value=date.today())
            new_loc = st.text_input("Nova lokacija")
            submit_fix = st.form_submit_button("Spremi ispravak")
        if submit_fix and p_sel and d_sel and new_loc:
            row_fix = pd.DataFrame([{
                "Datum": d_sel.strftime("%d.%m.%Y."),
                "Dan": HR_DAYS[pd.Timestamp(d_sel).weekday()],
                "Ime i prezime": p_sel,
                "Odjel": df_emp.loc[df_emp['Name']==p_sel, 'Department'].iloc[0] if not df_emp.empty else "",
                "Lokacija": new_loc,
                "Week": pd.Timestamp(d_sel).isocalendar().week,
                "Month": d_sel.month,
                "Year": d_sel.year,
            }])
            save_tracker_rows(row_fix)

# ---------- Weekly entry ----------
def monday_of_week(d:date)->date: return (pd.Timestamp(d)-pd.Timedelta(days=d.weekday())).date()
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

def iso_week(dt:date)->int: return pd.Timestamp(dt).isocalendar().week
def week_bounds(monday:date):
    end=(pd.Timestamp(monday)+pd.Timedelta(days=6)).date(); return monday, end

week_monday=(pd.Timestamp(today)-pd.Timedelta(days=today.weekday())+pd.Timedelta(weeks=st.session_state.week_offset)).date()
week_start, week_end = week_bounds(week_monday); week_num=iso_week(week_monday)
week_year = pd.Timestamp(week_monday).isocalendar().year
st.subheader(f"Tjedan {week_num} ({week_year}) ({week_start.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

tracker_all = df_init
prefill={}
if not tracker_all.empty:
    t=with_parsed_date(normalize_columns(tracker_all.copy()))
    mask=(t['Ime i prezime']==full_name) & (t['Datum_dt']>=pd.Timestamp(week_start)) & (t['Datum_dt']<=pd.Timestamp(week_end))
    for _,r in t[mask].iterrows(): prefill[r['Datum_dt'].date()]=str(r['Lokacija'])

if copy_last and not tracker_all.empty:
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
        val=""
        d=(pd.Timestamp(week_start)+pd.Timedelta(days=i)).date()
        day_name=HR_DAYS[i]; hol=HOLIDAYS.get(d)
        c1,c2,c3=st.columns([2,2,3])
        if hol:
            with c1: st.markdown(f"<div class='hday-cell hday-left'><span class='label-strong'>Datum:</span> {pd.Timestamp(d).strftime('%d.%m.%Y.')}</div>", unsafe_allow_html=True)
            with c2: st.markdown(f"<div class='hday-cell'><span class='label-strong'>Dan:</span> {day_name}</div>", unsafe_allow_html=True)
            with c3: st.markdown(f"<div class='hday-cell hday-right'><span class='label-strong'>Lokacija:</span> {hol}</div>", unsafe_allow_html=True)
            val=hol
        else:
            with c1: st.markdown(f"**Datum:** {pd.Timestamp(d).strftime('%d.%m.%Y.')}")
            with c2: st.markdown(f"**Dan:** {day_name}")
            with c3:
                default=prefill.get(d,"")
                if locked:
                    st.text_input("Lokacija (zaključano)", value=default, disabled=True, key=f"lock_{d.isoformat()}")
                    val=default
                else:
                    sel=st.selectbox("Lokacija", ["(odaberi)"]+st.session_state.get('LOCATIONS', [])+["(drugo)"] if 'LOCATIONS' in st.session_state else ["(odaberi)"]+LOCATIONS+["(drugo)"],
                                     index=0,
                                     key=f"sel_{d.isoformat()}")
                    if sel=="(drugo)":
                        val=st.text_input("Ručni unos lokacije", value=default if default not in LOCATIONS else "", key=f"free_{d.isoformat()}").strip()
                        note=st.text_input("Napomena (obavezno za 'drugo')", key=f"note_{d.isoformat()}").strip()
                        other_notes[d]=note
                    elif sel!="(odaberi)": val=sel
                    else: val=default
                    if val and val.strip().lower()=="neradni dan": st.warning("Vrijednost 'Neradni dan' nije dopuštena za unos."); val=""
        if not hol and is_remote_value(val): remote_count+=1
        if not hol and not locked and not val: any_empty=True
        week_rows.append({"Datum":pd.Timestamp(d).strftime("%d.%m.%Y."),"Dan":day_name,"Ime i prezime":full_name,"Odjel":dept,"Lokacija":val,
                          "Week":pd.Timestamp(d).isocalendar().week,"Month":d.month,"Year":d.year})
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
            if other_notes:
                df_save['Napomena']=''
                for idx,r in df_save.iterrows():
                    dstr=r['Datum']; ddate=pd.to_datetime(dstr, dayfirst=True).date()
                    if ddate in other_notes and r['Lokacija'] and r['Lokacija'] not in LOCATIONS:
                        df_save.at[idx,'Napomena']=other_notes[ddate]
            st.session_state['last_saved_backup']=prefill
            save_tracker_rows(df_save)

if st.session_state.get('last_saved_backup'):
    if st.button("↩️ Vrati posljednje spremanje"):
        old=st.session_state['last_saved_backup']
        rows=[{"Datum": pd.Timestamp(d).strftime("%d.%m.%Y."),
               "Dan": HR_DAYS[pd.Timestamp(d).weekday()],
               "Ime i prezime": full_name, "Odjel": dept, "Lokacija": loc,
               "Week": pd.Timestamp(d).isocalendar().week, "Month": d.month, "Year": d.year} for d,loc in old.items()]
        if rows: save_tracker_rows(pd.DataFrame(rows))

# ---------- Personal analytics ----------
st.markdown("---"); st.subheader("Udio lokacija u tekućoj godini (na temelju spremljenih unosa)")
with st.spinner("Računam osobnu analitiku …"):
    tracker_df = df_init
    if not tracker_df.empty:
        t=with_parsed_date(normalize_columns(tracker_df)); mine=t[(t["Ime i prezime"]==full_name) & (t["Godina"]==date.today().year)]
        if not mine.empty:
            counts=mine["Lokacija"].value_counts()
            fig,ax=plt.subplots(figsize=(5.2,5.2)); wedges,_=ax.pie(counts.values, startangle=90); ax.axis("equal")
            total=int(counts.sum()); labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
            ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1,0.5)); st.pyplot(fig)
        else: st.info("Nema spremljenih unosa za tekuću godinu.")
    else: st.info("Još nema podataka u Tracker.csv.")

# ---------- Past records ----------
st.markdown("---"); st.subheader("📜 Vaši prijašnji zapisi")
with st.spinner("Učitavam prijašnje zapise …"):
    tracker_all=df_init
    if not tracker_all.empty:
        mine=tracker_all[tracker_all["Ime i prezime"]==full_name].copy()
        try: mine["_d"]=pd.to_datetime(mine.get("date_iso", mine["Datum"]), errors="coerce"); mine=mine.sort_values("_d", ascending=False).drop(columns="_d")
        except Exception: pass
        show=[c for c in ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year"] if c in mine.columns]
        if show: st.dataframe(mine[show], width='stretch', hide_index=True)
        else: st.info("Nema podataka za prikaz.")
    else: st.info("Tracker.csv je prazan ili nedostupan.")

st.caption("Centralni dnevnik je **data/Tracker.csv** (DESC + last-wins, canonical). Lokalni keš: data/Tracker.local.csv. Pending queue: data/Tracker.pending.csv.")
