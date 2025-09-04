# app.py (v7) — GitHub path: data/Tracker.csv, CSV sep: ';'
from pathlib import Path
from datetime import date, timedelta
import base64, io, requests, re
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

EMP_FILE = "Popis_djelatnika_HR_Sales.csv"   # ; separator (Name, Department, eMail)
LOC_FILE = "Locations.csv"                    # CP-1250, prva kolona = lokacije
HOL_FILE = "CroatianHolidays.csv"            # ; separator: Datum | Državni praznik | (Dan)

# Lokalni fallback/keš: uvijek postoji čak i bez GitHuba
LOCAL_FALLBACK_LOG = Path("data/Tracker.local.csv")

MAX_WEEKS_BACK = 2
MAX_WEEKS_FWD = 8
HR_DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]

st.set_page_config(page_title="Praćenje lokacije rada", page_icon="🗺️", layout="wide")

# ---------- GitHub helpers ----------
def gh_enabled() -> bool:
    return "GITHUB" in st.secrets and "token" in st.secrets["GITHUB"] and "repo" in st.secrets["GITHUB"]

def _gh_headers():
    token = st.secrets["GITHUB"]["token"]
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _gh_config():
    s = st.secrets["GITHUB"]
    return {
        "repo": s["repo"],
        "branch": s.get("branch", "main"),
        "path": s.get("path", "data/Tracker.csv"),            # ← default na data/Tracker.csv
        "committer_name": s.get("committer_name", "Streamlit Bot"),
        "committer_email": s.get("committer_email", "bot@example.com"),
        "csv_sep": s.get("csv_sep", ";"),                     # ← default na ';'
    }

def gh_get_file(repo, path, branch):
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=_gh_headers(), timeout=30)
    if r.status_code == 200:
        j = r.json()
        content_b64 = j["content"]
        sha = j["sha"]
        content = base64.b64decode(content_b64)
        return content, sha
    elif r.status_code == 404:
        return None, None
    else:
        raise RuntimeError(f"GitHub GET error: {r.status_code} {r.text}")

def gh_put_file(repo, path, branch, content_bytes, message, sha=None, committer_name=None, committer_email=None):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    data = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        data["sha"] = sha
    if committer_name and committer_email:
        data["committer"] = {"name": committer_name, "email": committer_email}
    r = requests.put(url, headers=_gh_headers(), json=data, timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT error: {r.status_code} {r.text}")
    return r.json()

def parse_csv_bytes(b: bytes, preferred_sep=";"):
    seps = [preferred_sep] + [s for s in [",", ";", "\t", "|"] if s != preferred_sep]
    for sep in seps:
        try:
            df = pd.read_csv(io.BytesIO(b), sep=sep, engine="python")
            if df.shape[1] >= 3:
                return df, sep
        except Exception:
            continue
    try:
        df = pd.read_csv(io.BytesIO(b), sep=None, engine="python")
        return df, None
    except Exception as e:
        raise

# ---------- CSV loaders (employees / locations / holidays) ----------
def read_csv_smart(path: str, seps=(',', ';'), encs=('utf-8', 'utf-8-sig', 'cp1250', 'latin1')) -> pd.DataFrame:
    last_err = None
    for sep in seps:
        for enc in encs:
            try:
                return pd.read_csv(path, sep=sep, encoding=enc)
            except Exception as e:
                last_err = e
    raise last_err

def load_employees(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        st.error(f"Nije pronađen '{path}'.")
        st.stop()
    df = read_csv_smart(path, seps=(';', ','))
    needed = {'Name', 'Department', 'eMail'}
    if not needed.issubset(set(df.columns)):
        st.error(f"U '{path}' moraju postojati kolone: {', '.join(sorted(needed))}.")
        st.stop()
    df['eMail_lc'] = df['eMail'].astype(str).str.strip().str.lower()
    return df[['Name', 'Department', 'eMail', 'eMail_lc']]

def clean_location(s: str) -> str:
    if not isinstance(s, str):
        return ''
    return s.strip().strip(' )').strip()

def load_locations(path: str):
    p = Path(path)
    if not p.exists():
        st.warning(f"Nije pronađen '{path}'. Koristim zadani set lokacija.")
        return ['Ured', 'Remote', 'Na terenu']
    encs = ('cp1250', 'utf-8', 'utf-8-sig', 'latin1')
    df = None
    for enc in encs:
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception:
            continue
    if df is None:
        st.error(f"Ne mogu učitati '{path}'.")
        st.stop()
    first_col = df.columns[0]
    vals = [clean_location(x) for x in df[first_col].astype(str).tolist()]
    out = []
    for v in vals:
        if not v:
            continue
        if v.lower() == 'neradni dan':
            continue
        if v not in out:
            out.append(v)
    return out

@st.cache_resource
def load_holidays_csv(path: str):
    p = Path(path)
    if not p.exists():
        st.error(f"Nije pronađen '{path}'. Postavite CroatianHolidays.csv u root.")
        st.stop()
    encs = ('utf-8', 'utf-8-sig', 'cp1250', 'latin1')
    df = None
    for enc in encs:
        try:
            df = pd.read_csv(path, sep=';', encoding=enc)
            break
        except Exception:
            continue
    if df is None:
        st.error(f"Ne mogu učitati '{path}'.")
        st.stop()

    cols_lower = {c.lower(): c for c in df.columns}
    date_col = None
    name_col = None
    for key in cols_lower:
        if "datum" in key or "date" in key:
            date_col = cols_lower[key]
        if "praznik" in key or "holiday" in key or "naziv" in key or "name" in key:
            name_col = cols_lower[key]
    if date_col is None:
        date_col = df.columns[0]
    if name_col is None:
        name_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    df["_date"] = pd.to_datetime(df[date_col].astype(str).str.strip(), dayfirst=True, errors="coerce").dt.date
    df["_name"] = df[name_col].astype(str).str.strip()

    lookup = {}
    for _, r in df.dropna(subset=["_date"]).iterrows():
        lookup[r["_date"]] = r["_name"]
    return lookup

# ---------- Tracker helpers ----------
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Normalizacija naziva kolona
    ren = {}
    for c in df.columns:
        key = re.sub(r"\s+", " ", c.strip()).lower()
        ren[c] = key
    t = df.rename(columns=ren)
    mapping = {}
    for c in t.columns:
        if c == "datum" or "datum" in c:
            mapping[c] = "Datum"
        elif ("ime" in c and "prezime" in c) or c in ["ime i prezime"]:
            mapping[c] = "Ime i prezime"
        elif "odjel" in c:
            mapping[c] = "Odjel"
        elif "lokacija" in c:
            mapping[c] = "Lokacija"
        elif c == "week":
            mapping[c] = "Week"
        elif c == "month":
            mapping[c] = "Month"
        elif c == "year":
            mapping[c] = "Year"
        else:
            mapping[c] = c  # ostavi kako je
    return t.rename(columns=mapping)

def dedupe_on_name_date(df: pd.DataFrame) -> pd.DataFrame:
    t = df.copy()
    if "Datum" in t.columns:
        t["Datum_norm"] = pd.to_datetime(t["Datum"], dayfirst=True, errors="coerce").dt.strftime("%d.%m.%Y.")
    else:
        t["Datum_norm"] = None
    if "Ime i prezime" in t.columns:
        t = t.drop_duplicates(subset=["Ime i prezime", "Datum_norm"], keep="last")
    return t

def load_tracker() -> pd.DataFrame:
    """Učitaj centralni Tracker.csv (GitHub-first; seed iz data/Tracker.csv ako ne postoji)."""
    if gh_enabled():
        cfg = _gh_config()
        content, _ = gh_get_file(cfg["repo"], cfg["path"], cfg["branch"])
        if content is None:
            # pokuša seedati iz lokalnog data/Tracker.csv, pa iz root/Tracker.csv
            for local_seed in [Path("data/Tracker.csv"), Path("Tracker.csv")]:
                if local_seed.exists():
                    gh_put_file(cfg["repo"], cfg["path"], cfg["branch"], local_seed.read_bytes(),
                                message="Seed data/Tracker.csv from app",
                                committer_name=cfg["committer_name"], committer_email=cfg["committer_email"])
                    content, _ = gh_get_file(cfg["repo"], cfg["path"], cfg["branch"])
                    break
            if content is None:
                cols = ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year"]
                return pd.DataFrame(columns=cols)
        try:
            gh_df, _ = parse_csv_bytes(content, preferred_sep=_gh_config()["csv_sep"])
        except Exception as e:
            st.error(f"Ne mogu parsirati CSV sa GitHuba: {e}")
            st.stop()
        gh_df = normalize_columns(gh_df)
        try:
            LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
            gh_df.to_csv(LOCAL_FALLBACK_LOG, index=False)
        except Exception:
            pass
        return gh_df
    else:
        if LOCAL_FALLBACK_LOG.exists():
            df = pd.read_csv(LOCAL_FALLBACK_LOG)
            return normalize_columns(df)
        else:
            cols = ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year"]
            return pd.DataFrame(columns=cols)

def save_tracker(new_rows: pd.DataFrame):
    existing = load_tracker()
    nr = normalize_columns(new_rows)
    merged = pd.concat([existing, nr], ignore_index=True)
    merged = dedupe_on_name_date(merged)

    # lokalni fallback
    try:
        LOCAL_FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(LOCAL_FALLBACK_LOG, index=False)
    except Exception:
        pass

    # GitHub upis
    if gh_enabled():
        cfg = _gh_config()
        pref_cols = ["Datum","Ime i prezime","Odjel","Lokacija","Week","Month","Year"]
        cols = [c for c in pref_cols if c in merged.columns] + [c for c in merged.columns if c not in pref_cols]
        merged = merged[cols]
        out_bytes = merged.to_csv(index=False, sep=cfg["csv_sep"]).encode("utf-8")
        content, sha = gh_get_file(cfg["repo"], cfg["path"], cfg["branch"])
        gh_put_file(cfg["repo"], cfg["path"], cfg["branch"], out_bytes,
                    message="Update data/Tracker.csv from Streamlit",
                    sha=sha,
                    committer_name=cfg["committer_name"], committer_email=cfg["committer_email"])
    else:
        st.info("GitHub sync nije konfiguriran; ažurirana je samo lokalna kopija (data/Tracker.local.csv).")

# ---------- App: employees / locations / holidays ----------
def load_employees_and_support():
    emp = load_employees(EMP_FILE)
    locs = load_locations(LOC_FILE)
    hols = load_holidays_csv(HOL_FILE)
    return emp, locs, hols

employees, LOCATIONS, HOLIDAYS = load_employees_and_support()

today = date.today()

# ---------- UI ----------
st.header("👋 Dobrodošli")
st.write(f"Danas je: **{today.strftime('%d.%m.%Y.')}**")
email = st.text_input("Unesite svoju eMail adresu").strip().lower()
if not email:
    st.stop()

row = employees[employees['eMail_lc'] == email]
if row.empty:
    st.error("E-mail nije pronađen u popisu djelatnika.")
    st.stop()

person = row.iloc[0]
full_name = str(person['Name'])
dept = str(person['Department'])

st.success(f"Pozdrav, **{full_name}** ({dept})! Unesite lokaciju rada za odabrani tjedan.")

if 'week_offset' not in st.session_state:
    st.session_state.week_offset = 1  # sljedeći tjedan

cols_nav = st.columns([1,1,1,1])
with cols_nav[0]:
    if st.button("⬅️ Prethodni tjedan", disabled=st.session_state.week_offset <= -MAX_WEEKS_BACK):
        st.session_state.week_offset -= 1
with cols_nav[1]:
    if st.button("📅 Ovaj tjedan"):
        st.session_state.week_offset = 0
with cols_nav[2]:
    if st.button("⏭️ Sljedeći tjedan"):
        st.session_state.week_offset = 1
with cols_nav[3]:
    if st.button("➡️ Sljedeći ➕", disabled=st.session_state.week_offset >= MAX_WEEKS_FWD):
        st.session_state.week_offset += 1

def get_week_monday(ref: date, offset_weeks: int) -> date:
    return (pd.Timestamp(ref) - pd.Timedelta(days=ref.weekday()) + pd.Timedelta(weeks=offset_weeks)).date()

def iso_week(dt: date) -> int:
    return pd.Timestamp(dt).isocalendar().week

week_monday = get_week_monday(today, st.session_state.week_offset)
week_end = (pd.Timestamp(week_monday) + pd.Timedelta(days=6)).date()
week_num = iso_week(week_monday)
st.subheader(f"Tjedan {week_num} ({week_monday.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

# Učitaj centralni tracker (GitHub-first)
tracker_df = load_tracker()

# Prefill postojeće unose za taj tjedan (ovog korisnika)
prefill = {}
if not tracker_df.empty:
    t = tracker_df.copy()
    t = normalize_columns(t)
    try:
        t['Datum_d'] = pd.to_datetime(t['Datum'], dayfirst=True, errors='coerce').dt.date
    except Exception:
        t['Datum_d'] = t['Datum']
    mask = (t['Ime i prezime'] == full_name) & (t['Year'] == week_monday.year) & (t['Week'] == week_num)
    for _, r in t[mask].iterrows():
        prefill[r['Datum_d']] = str(r['Lokacija'])

with st.form("unos_tjedan"):
    st.write("**A – Datum, B – Dan, C – Lokacija** (neradni dani dolaze iz CroatianHolidays.csv i nisu izmjenjivi).")

    week_rows = []
    for i in range(5):  # pon–pet
        d = (pd.Timestamp(week_monday) + pd.Timedelta(days=i)).date()
        day_name = HR_DAYS[i]
        hol = HOLIDAYS.get(d)

        c1, c2, c3 = st.columns([2,2,3])
        with c1:
            st.markdown(f"**A – Datum:** {pd.Timestamp(d).strftime('%d.%m.%Y.')}")
        with c2:
            st.markdown(f"**B – Dan:** {day_name}")
        with c3:
            default_val = prefill.get(d, "")
            if hol:
                st.text_input("C – Lokacija", value=hol, disabled=True, key=f"loc_{d.isoformat()}")
                value = hol
            else:
                sel = st.selectbox("C – Lokacija (pretraži ili odaberi)", ["(odaberi)"] + LOCATIONS + ["(upiši ručno)"],
                                   index=(["(odaberi)"] + LOCATIONS + ["(upiši ručno)"]).index(default_val) if default_val in LOCATIONS else 0,
                                   key=f"sel_{d.isoformat()}")
                if sel == "(upiši ručno)":
                    txt = st.text_input("Druga lokacija (ručni unos)", value=default_val, key=f"free_{d.isoformat()}")
                    value = txt.strip()
                elif sel != "(odaberi)":
                    value = sel
                else:
                    value = default_val

                if value and value.strip().lower() == "neradni dan":
                    st.warning("Vrijednost 'Neradni dan' nije dopuštena za unos.")
                    value = ""

            week_rows.append({
                "Datum": pd.Timestamp(d).strftime("%d.%m.%Y."),
                "Ime i prezime": full_name,
                "Odjel": dept,
                "Lokacija": value,
                "Week": iso_week(d),
                "Month": d.month,
                "Year": d.year,
            })

    if st.form_submit_button("💾 Spremi tjedne unose"):
        to_save = [r for r in week_rows if r["Lokacija"]]
        if not to_save:
            st.info("Nema unosa za spremanje.")
        else:
            save_tracker(pd.DataFrame(to_save))
            st.success("Unosi su spremljeni u data/Tracker.csv.")

st.markdown("---")
st.subheader("Udio lokacija u tekućoj godini (na temelju spremljenih unosa)")

tracker_df = load_tracker()
if not tracker_df.empty:
    t = normalize_columns(tracker_df)
    try:
        t["Datum"] = pd.to_datetime(t["Datum"], dayfirst=True, errors="coerce")
    except Exception:
        pass
    mine = t[(t["Ime i prezime"] == full_name) & (t["Year"] == date.today().year)]
    if not mine.empty:
        counts = mine["Lokacija"].value_counts()
        fig, ax = plt.subplots()
        ax.pie(counts.values, labels=counts.index, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
        st.pyplot(fig)
        st.caption("Graf prikazuje postotne udjele lokacija na kojima radite u tekućoj godini.")
    else:
        st.info("Nema spremljenih unosa za tekuću godinu.")
else:
    st.info("Još nema podataka u Tracker.csv.")

st.caption("Centralni dnevnik je **data/Tracker.csv** u GitHub repozitoriju (ako je konfiguriran). Lokalni keš: data/Tracker.local.csv.")
