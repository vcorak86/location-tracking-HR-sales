# app.py
import os
from pathlib import Path
from datetime import date, datetime, timedelta
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

EMP_FILE = "Popis_djelatnika_HR_Sales.csv"
LOC_FILE = "Locations.csv"
DATA_DIR = Path("data")
LOG_PATH = DATA_DIR / "radne_lokacije_log.csv"
MAX_WEEKS_BACK = 2
MAX_WEEKS_FWD = 8
HR_DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]

st.set_page_config(page_title="Praćenje lokacije rada", page_icon="🗺️", layout="wide")

def read_csv_smart(path: str, seps=(',', ';'), encs=('utf-8', 'utf-8-sig', 'cp1250', 'latin1')) -> pd.DataFrame:
    last_err = None
    for sep in seps:
        for enc in encs:
            try:
                return pd.read_csv(path, sep=sep, encoding=enc)
            except Exception as e:
                last_err = e
                continue
    raise last_err

def load_employees(path: str) -> pd.DataFrame:
    if not Path(path).exists():
        st.error(f"Nije pronađen '{path}'. Dodaj datoteku u root repozitorija.")
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
    s = s.strip().strip(' )').strip()
    return s

def load_locations(path: str):
    if not Path(path).exists():
        st.warning(f"Nije pronađen '{path}'. Koristim zadani set lokacija.")
        return ['Ured', 'Remote', 'Na terenu']
    encs = ('cp1250', 'utf-8', 'utf-8-sig', 'latin1')
    last_err = None
    for enc in encs:
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception as e:
            last_err = e
    else:
        raise last_err
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
def get_holiday_cache():
    try:
        from CroatianHolidays import CroatianHolidays
    except Exception:
        st.error("CroatianHolidays.py nije ispravno dostupan u rootu. Provjeri naziv i sadržaj.")
        st.stop()
    ch = CroatianHolidays()
    cache = {}
    return ch, cache

def holiday_name_for_date(d: date, ch, cache):
    y = d.year
    if y not in cache:
        try:
            cache[y] = ch.getHolidays(y)
        except Exception:
            cache[y] = {}
    key = d.strftime('%d. %m. %Y.')
    return cache[y].get(key)

def ensure_log():
    DATA_DIR.mkdir(exist_ok=True)
    if not LOG_PATH.exists():
        pd.DataFrame(columns=['Datum','Ime i prezime','Odjel','Lokacija','Week','Month','Year']).to_csv(LOG_PATH, index=False)

def load_log() -> pd.DataFrame:
    ensure_log()
    try:
        return pd.read_csv(LOG_PATH, parse_dates=['Datum'], dayfirst=True)
    except Exception:
        return pd.read_csv(LOG_PATH)

def save_entries(rows):
    ensure_log()
    cur = pd.read_csv(LOG_PATH)
    if not cur.empty:
        cur['Datum_norm'] = pd.to_datetime(cur['Datum'], dayfirst=True, errors='coerce').dt.strftime('%d.%m.%Y.')
    new = pd.DataFrame(rows)
    new['Datum_norm'] = pd.to_datetime(new['Datum'], dayfirst=True, errors='coerce').dt.strftime('%d.%m.%Y.')
    if not cur.empty:
        mask = ~cur.set_index(['Ime i prezime','Datum_norm']).index.isin(new.set_index(['Ime i prezime','Datum_norm']).index)
        cur = cur[mask].drop(columns=['Datum_norm'])
    new = new.drop(columns=['Datum_norm'])
    out = pd.concat([cur, new], ignore_index=True)
    out.to_csv(LOG_PATH, index=False)

def get_week_monday(ref: date, offset_weeks: int) -> date:
    return ref - timedelta(days=ref.weekday()) + timedelta(weeks=offset_weeks)

def iso_week(dt: date) -> int:
    return dt.isocalendar()[1]

# --- App ---
employees = load_employees(EMP_FILE)
LOCATIONS = load_locations(LOC_FILE)
ch, holiday_cache = get_holiday_cache()

today = date.today()

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

week_monday = get_week_monday(today, st.session_state.week_offset)
week_end = week_monday + timedelta(days=6)
week_num = iso_week(week_monday)
st.subheader(f"Tjedan {week_num} ({week_monday.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

log_df = load_log()

prefill = {}
if not log_df.empty:
    tmp = log_df.copy()
    try:
        tmp['Datum_d'] = pd.to_datetime(tmp['Datum'], dayfirst=True, errors='coerce').dt.date
    except Exception:
        tmp['Datum_d'] = tmp['Datum']
    mask = (tmp['Ime i prezime'] == full_name) & (tmp['Year'] == week_monday.year) & (tmp['Week'] == week_num)
    for _, r in tmp[mask].iterrows():
        prefill[r['Datum_d']] = str(r['Lokacija'])

with st.form("unos_tjedan"):
    st.write("**A – Datum, B – Dan, C – Lokacija** (neradni dani dolaze iz CroatianHolidays i nisu izmjenjivi).")

    week_rows = []
    for i in range(5):  # pon–pet
        d = week_monday + timedelta(days=i)
        day_name = HR_DAYS[i]
        hol = holiday_name_for_date(d, ch, holiday_cache)

        c1, c2, c3 = st.columns([2,2,3])
        with c1:
            st.markdown(f"**A – Datum:** {d.strftime('%d.%m.%Y.')}")
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
                "Datum": d.strftime("%d.%m.%Y."),
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
            save_entries(to_save)
            st.success("Unosi su spremljeni.")

st.markdown("---")
st.subheader("Udio lokacija u tekućoj godini (na temelju vaših spremljenih unosa)")

log_df = load_log()
if not log_df.empty:
    try:
        log_df["Datum"] = pd.to_datetime(log_df["Datum"], dayfirst=True, errors="coerce")
    except Exception:
        pass
    mine = log_df[(log_df["Ime i prezime"] == full_name) & (log_df["Year"] == today.year)]
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
    st.info("Još nema podataka u dnevniku.")

st.caption("Podaci i logovi spremaju se u **data/radne_lokacije_log.csv** (Datum | Ime i prezime | Odjel | Lokacija | Week | Month | Year).")
