# app.py (v8)
from pathlib import Path
from datetime import date, datetime
import base64, io, requests, re, math
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

EMP_FILE = "data/Popis_djelatnika_HR_Sales.csv"
LOC_FILE = "data/Locations.csv"
HOL_FILE = "data/CroatianHolidays.csv"
GH_TRACKER_PATH_DEFAULT = "data/Tracker.csv"

LOCAL_FALLBACK_LOG = Path("data/Tracker.local.csv")
DEFAULT_GH_SEP = ";"
HR_DAYS = ["Ponedjeljak","Utorak","Srijeda","Četvrtak","Petak"]
MONTHS_HR = ["Siječanj","Veljača","Ožujak","Travanj","Svibanj","Lipanj","Srpanj","Kolovoz","Rujan","Listopad","Studeni","Prosinac"]

st.set_page_config(page_title="Praćenje lokacije rada", page_icon="🗺️", layout="wide")

def gh_enabled(): return "GITHUB" in st.secrets and all(k in st.secrets["GITHUB"] for k in ["token","repo"])
def _gh_headers():
    return {"Authorization": f"Bearer {st.secrets['GITHUB']['token']}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
def _sanitize_repo(repo:str)->str: return repo.strip().strip("/")
def _gh_config():
    s=st.secrets["GITHUB"]
    return {"repo":_sanitize_repo(s["repo"]), "branch":s.get("branch","main"), "path":s.get("path", GH_TRACKER_PATH_DEFAULT),
            "committer_name":s.get("committer_name",None), "committer_email":s.get("committer_email",None),
            "csv_sep":s.get("csv_sep", DEFAULT_GH_SEP)}
def gh_repo_info(repo:str):
    r=requests.get(f"https://api.github.com/repos/{repo}", headers=_gh_headers(), timeout=30)
    return r.status_code, r.text
def gh_get_file(repo,path,branch):
    r=requests.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}", headers=_gh_headers(), timeout=30)
    if r.status_code==200:
        j=r.json(); return base64.b64decode(j["content"]), j["sha"]
    elif r.status_code==404: return None, None
    else: return {"status":r.status_code,"body":r.text}, None
def gh_put_file(repo,path,branch,content_bytes,message,sha=None,committer_name=None,committer_email=None):
    data={"message":message,"content":base64.b64encode(content_bytes).decode("utf-8"),"branch":branch}
    if sha: data["sha"]=sha
    if committer_name and committer_email: data["committer"]={"name":committer_name,"email":committer_email}
    r=requests.put(f"https://api.github.com/repos/{repo}/contents/{path}", headers=_gh_headers(), json=data, timeout=60)
    if r.status_code not in (200,201): return {"status":r.status_code,"body":r.text}
    return {"status":r.status_code,"body":"OK"}

def parse_csv_bytes(b:bytes, preferred_sep=DEFAULT_GH_SEP):
    for sep in [preferred_sep]+[s for s in [",",";","\t","|"] if s!=preferred_sep]:
        try:
            df=pd.read_csv(io.BytesIO(b), sep=sep, engine="python")
            if df.shape[1]>=3: return df, sep
        except Exception: pass
    df=pd.read_csv(io.BytesIO(b), sep=None, engine="python"); return df, None

def read_csv_smart(path:str, seps=(',', ';'), encs=('utf-8','utf-8-sig','cp1250','latin1')):
    last=None
    for sep in seps:
        for enc in encs:
            try: return pd.read_csv(path, sep=sep, encoding=enc)
            except Exception as e: last=e
    raise last

def load_employees(path:str)->pd.DataFrame:
    p=Path(path)
    if not p.exists(): st.error(f"Nije pronađen '{path}'."); st.stop()
    df=read_csv_smart(path, seps=(';',','))
    ren={c: re.sub(r"\s+"," ", str(c).strip()) for c in df.columns}
    df=df.rename(columns=ren)
    need={'Name','Department','eMail'}
    if not need.issubset(df.columns): st.error('Popis_djelatnika_HR_Sales.csv mora sadržavati kolone: Name, Department, eMail.'); st.stop()
    if 'Manager' not in df.columns: df['Manager']=''
    if 'Director' not in df.columns: df['Director']=''
    df['eMail_lc']=df['eMail'].astype(str).str.strip().str.lower()
    return df[['Name','Department','eMail','Manager','Director','eMail_lc']]

def clean_location(s:str)->str:
    if not isinstance(s,str): return ''
    return s.strip().strip(' )').strip()

def load_locations(path:str):
    p=Path(path)
    if not p.exists(): st.warning(f"Nije pronađen '{path}'. Koristim zadani set lokacija."); return ['Ured','Remote','Na terenu']
    df=None
    for enc in ('cp1250','utf-8','utf-8-sig','latin1'):
        try: df=pd.read_csv(path, encoding=enc); break
        except Exception: pass
    if df is None: st.error(f"Ne mogu učitati '{path}'."); st.stop()
    vals=[clean_location(x) for x in df[df.columns[0]].astype(str).tolist()]
    out=[]
    for v in vals:
        if not v: continue
        if v.lower()=='neradni dan': continue
        if v not in out: out.append(v)
    return out

@st.cache_resource
def load_holidays_csv(path:str):
    p=Path(path)
    if not p.exists(): st.error(f"Nije pronađen '{path}'. Postavite CroatianHolidays.csv u data/."); st.stop()
    df=None
    for enc in ('utf-8','utf-8-sig','cp1250','latin1'):
        try: df=pd.read_csv(path, sep=';', encoding=enc); break
        except Exception: pass
    if df is None: st.error(f"Ne mogu učitati '{path}'."); st.stop()
    cols_lower={c.lower():c for c in df.columns}
    date_col=next((cols_lower[k] for k in cols_lower if 'datum'in k or 'date'in k), df.columns[0])
    name_col=next((cols_lower[k] for k in cols_lower if 'praznik'in k or 'holiday'in k or 'naziv'in k or 'name'in k), df.columns[1] if len(df.columns)>1 else df.columns[0])
    df['_date']=pd.to_datetime(df[date_col].astype(str).str.strip(), dayfirst=True, errors='coerce').dt.date
    df['_name']=df[name_col].astype(str).str.strip()
    return {r['_date']:r['_name'] for _,r in df.dropna(subset=['_date']).iterrows()}

def normalize_columns(df:pd.DataFrame)->pd.DataFrame:
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
    if not t.columns.is_unique: t=t.loc[:, ~t.columns.duplicated(keep='first')]
    return t

def with_parsed_date(df:pd.DataFrame)->pd.DataFrame:
    t=df.copy()
    t['Datum_dt']=pd.to_datetime(t.get('Datum',''), dayfirst=True, errors='coerce')
    t['Godina']=t['Datum_dt'].dt.year
    t['Mjesec']=t['Datum_dt'].dt.month
    t['Kvartal']=((t['Mjesec']-1)//3 + 1)
    return t

def dedupe_last_then_sort_desc(df:pd.DataFrame)->pd.DataFrame:
    t=with_parsed_date(normalize_columns(df))
    t['Datum_key']=t['Datum_dt'].dt.strftime('%d.%m.%Y.')
    if 'Ime i prezime' in t.columns:
        t=t.drop_duplicates(subset=['Ime i prezime','Datum_key'], keep='last')
    t=t.sort_values(['Datum_dt'], ascending=[False], na_position='last')
    return t.drop(columns=['Datum_key'], errors='ignore')

def load_tracker()->pd.DataFrame:
    if gh_enabled():
        cfg=_gh_config()
        status_repo, body_repo = gh_repo_info(cfg['repo'])
        if status_repo not in (200,301):
            st.error(f"Repo '{cfg['repo']}' nije dostupan (status {status_repo})."); st.caption(body_repo)
            return pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
        content, sha_or_dict = gh_get_file(cfg['repo'], cfg['path'], cfg['branch'])
        if isinstance(content, dict):
            st.error(f"GitHub GET error {content['status']} za '{cfg['path']}' na grani '{cfg['branch']}'."); st.caption(content['body'])
            return pd.read_csv(LOCAL_FALLBACK_LOG) if LOCAL_FALLBACK_LOG.exists() else pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
        if content is None:
            seed=Path(GH_TRACKER_PATH_DEFAULT)
            if seed.exists():
                put=gh_put_file(cfg['repo'], cfg['path'], cfg['branch'], seed.read_bytes(), "Seed Tracker.csv from app",
                                committer_name=cfg['committer_name'], committer_email=cfg['committer_email'])
                if put.get('status') in (200,201): content,_=gh_get_file(cfg['repo'], cfg['path'], cfg['branch'])
            if content is None: return pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])
        gh_df,_=parse_csv_bytes(content, preferred_sep=_gh_config()['csv_sep'])
        gh_df=dedupe_last_then_sort_desc(gh_df)
        try: LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True); gh_df.to_csv(LOCAL_FALLBACK_LOG, index=False)
        except Exception: pass
        return gh_df
    else:
        if LOCAL_FALLBACK_LOG.exists(): return dedupe_last_then_sort_desc(pd.read_csv(LOCAL_FALLBACK_LOG))
        return pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year'])

def save_tracker(new_rows:pd.DataFrame):
    existing=load_tracker()
    merged=pd.concat([existing, new_rows], ignore_index=True)
    merged=dedupe_last_then_sort_desc(merged)
    try: LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True); merged.to_csv(LOCAL_FALLBACK_LOG, index=False)
    except Exception: pass
    if gh_enabled():
        cfg=_gh_config()
        pref=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year']
        cols=[c for c in pref if c in merged.columns]+[c for c in merged.columns if c not in pref]
        out=merged[cols].to_csv(index=False, sep=cfg['csv_sep']).encode('utf-8')
        content, sha = gh_get_file(cfg['repo'], cfg['path'], cfg['branch'])
        if isinstance(content, dict): st.error(f"GitHub GET error {content['status']} pri spremanju."); st.caption(content['body']); return
        put=gh_put_file(cfg['repo'], cfg['path'], cfg['branch'], out, "Update Tracker.csv (DESC, last-wins) from Streamlit", sha,
                        cfg['committer_name'], cfg['committer_email'])
        if put.get('status') not in (200,201): st.error(f"GitHub PUT error {put.get('status')}"); st.code(put.get('body'))
    else: st.info("GitHub sync nije konfiguriran; ažurirana je samo lokalna kopija (data/Tracker.local.csv).")

def monday_of_week(d:date)->date: return (pd.Timestamp(d)-pd.Timedelta(days=d.weekday())).date()
def weeks_forward_until_year_end(ref:date)->int:
    year_end=date(ref.year,12,31)
    start_monday=monday_of_week(ref); end_monday=monday_of_week(year_end)
    delta_days=(pd.Timestamp(end_monday)-pd.Timestamp(start_monday)).days
    return max(0, int(delta_days//7))

def render_location_chart(counts:pd.Series, chart_type:str='Pie'):
    if counts.empty: st.info("Nema podataka za prikaz."); return
    if chart_type=='Pie':
        fig,ax=plt.subplots(figsize=(5.2,5.2))
        wedges,_=ax.pie(counts.values, startangle=90); ax.axis('equal')
        total=int(counts.sum()); labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
        ax.legend(wedges, labels, loc='center left', bbox_to_anchor=(1,0.5))
        st.pyplot(fig)
    elif chart_type=='Column':
        fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.bar(counts.index.astype(str), counts.values)
        ax.set_xlabel('Lokacija'); ax.set_ylabel('Broj'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)
    elif chart_type=='Bar':
        fig,ax=plt.subplots(figsize=(6.5,4.2)); ax.barh(counts.index.astype(str), counts.values)
        ax.set_xlabel('Broj'); ax.set_ylabel('Lokacija'); ax.set_title('Raspodjela po lokaciji'); st.pyplot(fig)

def styled_holiday_box(text:str):
    st.markdown(f"""<div style="padding:10px; border-radius:10px; border:1px solid #f1c40f33; background:#fff8e1;"><strong>''' +  + text +  + </strong></div>""", unsafe_allow_html=True)

# Base data
employees=load_employees(EMP_FILE)
LOCATIONS=load_locations(LOC_FILE)
HOLIDAYS=load_holidays_csv(HOL_FILE)

# Title + email first
st.title("Dobrodošli u aplikaciju za praćenje lokacije rada!")
email=st.text_input("Unesite svoju eMail adresu").strip().lower()
if not email: st.stop()

row=employees[employees['eMail_lc']==email]
if row.empty: st.error("E-mail nije pronađen u popisu djelatnika."); st.stop()

person=row.iloc[0]; full_name=str(person['Name']); dept=str(person['Department'])
st.success(f"Pozdrav, **{full_name}** ({dept})!")

# Admin portal (hidden until after email)
with st.expander("🛠️ Admin portal (PIN 1986)"):
    pin=st.text_input("Unesite admin PIN", type="password", value="")
    if pin=="1986":
        st.success("Admin pristup odobren.")
        all_data=with_parsed_date(normalize_columns(load_tracker().copy()))
        try:
            name2mgr=employees.set_index('Name')[['Manager','Department']]
            all_data=all_data.merge(name2mgr, left_on='Ime i prezime', right_index=True, how='left')
        except Exception: pass

        c_year, c_quarter, c_month = st.columns([1,1,2])
        with c_year:
            years=sorted([int(y) for y in all_data['Godina'].dropna().unique() if str(y).isdigit()], reverse=True)
            sel_years=st.multiselect("Godina", options=years, default=years[:1] if years else [])
        with c_quarter:
            sel_quarters=st.multiselect("Kvartal", options=[1,2,3,4], default=[])
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

        st.markdown("---"); st.subheader("Analitika lokacija")
        chart_type=st.radio("Prikaz", ["Pie","Column","Bar"], horizontal=True, index=0)
        if not df.empty and 'Lokacija' in df.columns:
            counts=df['Lokacija'].value_counts()
            render_location_chart(counts, chart_type=chart_type)
            total=counts.sum()
            table=pd.DataFrame({"Lokacija":counts.index,"Broj":counts.values,"Postotak":(counts.values/total*100).round(1)})
            st.dataframe(table, width='stretch', hide_index=True)
        else: st.info("Nema podataka za odabrane filtere.")

        st.markdown("---"); st.subheader("🔌 Test connection")
        if gh_enabled():
            cfg=_gh_config()
            if st.button("▶️ GET repo & PUT check"):
                status_repo,_=gh_repo_info(cfg['repo']); st.write(f"Repo status: {status_repo}")
                test_path="data/connection_check.txt"; payload=f"Connection OK at {datetime.utcnow().isoformat()}Z".encode("utf-8")
                content, sha = gh_get_file(cfg['repo'], test_path, cfg['branch'])
                if isinstance(content, dict): st.error(f"GET error {content['status']}"); st.code(content["body"])
                else:
                    put_res=gh_put_file(cfg['repo'], test_path, cfg['branch'], payload, "Connection check from Streamlit", sha,
                                        cfg['committer_name'], cfg['committer_email'])
                    if put_res.get("status") in (200,201): st.success("PUT test: OK (data/connection_check.txt)")
                    else: st.error(f"PUT test error {put_res.get('status')}"); st.code(put_res.get("body"))
        else: st.warning("GITHUB secrets nisu postavljeni.")
    elif pin: st.error("Neispravan PIN.")

# User weekly entry
today=date.today()
MAX_WEEKS_FWD=weeks_forward_until_year_end(today)
if 'week_offset' not in st.session_state: st.session_state.week_offset=1

cols_nav=st.columns([1,1,1,1])
with cols_nav[0]:
    if st.button("⬅️ Prethodni tjedan", disabled=st.session_state.week_offset<=-2): st.session_state.week_offset-=1
with cols_nav[1]:
    if st.button("📅 Ovaj tjedan"): st.session_state.week_offset=0
with cols_nav[2]:
    if st.button("⏭️ Sljedeći tjedan"): st.session_state.week_offset=1
with cols_nav[3]:
    if st.button("➡️ Sljedeći ➕", disabled=st.session_state.week_offset>=MAX_WEEKS_FWD): st.session_state.week_offset+=1

def iso_week(dt:date)->int: return pd.Timestamp(dt).isocalendar().week
def week_bounds(monday:date):
    end=(pd.Timestamp(monday)+pd.Timedelta(days=6)).date(); return monday, end

week_monday=(pd.Timestamp(today)-pd.Timedelta(days=today.weekday())+pd.Timedelta(weeks=st.session_state.week_offset)).date()
week_start, week_end = week_bounds(week_monday); week_num=iso_week(week_monday)
st.subheader(f"Tjedan {week_num} ({week_start.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

tracker_all=load_tracker()
prefill={}
if not tracker_all.empty:
    t=tracker_all.copy()
    try: t['Datum_d']=pd.to_datetime(t['Datum'], dayfirst=True, errors='coerce').dt.date
    except Exception: t['Datum_d']=t['Datum']
    mask=(t['Ime i prezime']==full_name) & (t['Datum_d']>=week_start) & (t['Datum_d']<=week_end)
    for _,r in t[mask].iterrows(): prefill[r['Datum_d']]=str(r['Lokacija'])

with st.form("unos_tjedan"):
    st.write("**Datum, Dan, Lokacija** — Neradni dani su automatski označeni i nisu promjenjivi.")
    week_rows=[]
    for i in range(5):
        d=(pd.Timestamp(week_start)+pd.Timedelta(days=i)).date()
        day_name=HR_DAYS[i]; hol=HOLIDAYS.get(d)
        if hol: st.markdown(f"<div style='padding:10px; border-radius:10px; border:1px solid #f1c40f33; background:#fff8e1;'><strong>{pd.Timestamp(d).strftime('%d.%m.%Y.')} — {day_name}: {hol}</strong></div>", unsafe_allow_html=True)
        c1,c2,c3=st.columns([2,2,3])
        with c1: st.markdown(f"**Datum:** {pd.Timestamp(d).strftime('%d.%m.%Y.')}")
        with c2: st.markdown(f"**Dan:** {day_name}")
        with c3:
            default=prefill.get(d,"")
            if hol:
                st.text_input("Lokacija", value=hol, disabled=True, key=f"loc_{d.isoformat()}"); val=hol
            else:
                sel=st.selectbox("Lokacija (pretraži ili odaberi)", ["(odaberi)"]+LOCATIONS+["(upiši ručno)"],
                                 index=(["(odaberi)"]+LOCATIONS+["(upiši ručno)"]).index(default) if default in LOCATIONS else 0,
                                 key=f"sel_{d.isoformat()}")
                if sel=="(upiši ručno)": val=st.text_input("Druga lokacija (ručni unos)", value=default, key=f"free_{d.isoformat()}").strip()
                elif sel!="(odaberi)": val=sel
                else: val=default
                if val and val.strip().lower()=="neradni dan": st.warning("Vrijednost 'Neradni dan' nije dopuštena za unos."); val=""
        week_rows.append({"Datum":pd.Timestamp(d).strftime("%d.%m.%Y."),"Ime i prezime":full_name,"Odjel":dept,"Lokacija":val,
                          "Week":iso_week(d),"Month":d.month,"Year":d.year})
    if st.form_submit_button("💾 Spremi tjedne unose"):
        to_save=[r for r in week_rows if r["Lokacija"]]
        if not to_save: st.info("Nema unosa za spremanje.")
        else: save_tracker(pd.DataFrame(to_save)); st.success("Unosi su spremljeni u Tracker.csv (GitHub ili lokalni keš).")

st.markdown("---"); st.subheader("Udio lokacija u tekućoj godini (na temelju spremljenih unosa)")
tracker_df=load_tracker()
if not tracker_df.empty:
    t=with_parsed_date(normalize_columns(tracker_df)); mine=t[(t["Ime i prezime"]==full_name) & (t["Godina"]==date.today().year)]
    if not mine.empty: 
        counts=mine["Lokacija"].value_counts()
        fig,ax=plt.subplots(figsize=(5.2,5.2)); wedges,_=ax.pie(counts.values, startangle=90); ax.axis("equal")
        total=int(counts.sum()); labels=[f"{n} — {v} ({v/total*100:.1f}%)" for n,v in zip(counts.index, counts.values)]
        ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(1,0.5)); st.pyplot(fig)
    else: st.info("Nema spremljenih unosa za tekuću godinu.")
else: st.info("Još nema podataka u Tracker.csv.")

st.markdown("---"); st.subheader("📜 Vaši prijašnji zapisi")
tracker_all=load_tracker()
if not tracker_all.empty:
    mine=tracker_all[tracker_all["Ime i prezime"]==full_name].copy()
    try: mine["_d"]=pd.to_datetime(mine["Datum"], dayfirst=True, errors="coerce"); mine=mine.sort_values("_d", ascending=False).drop(columns="_d")
    except Exception: pass
    show=[c for c in ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year"] if c in mine.columns]
    if show: st.dataframe(mine[show], width='stretch', hide_index=True)
    else: st.info("Nema podataka za prikaz.")
else: st.info("Tracker.csv je prazan ili nedostupan.")

st.caption("Centralni dnevnik je **data/Tracker.csv** (sortiran od najnovijeg prema najstarijem). Lokalni keš: data/Tracker.local.csv.")
