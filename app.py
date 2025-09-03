import os
from pathlib import Path
from datetime import date, datetime, timedelta
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt

EMP_CSV_PATHS = ["Popis_djelatnika_HR_Sales.csv", os.environ.get("EMP_CSV_PATH", "")]
LOKACIJE_PATHS = ["Lokacije.csv", os.environ.get("LOKACIJE_PATH", "")]
DATA_DIR = Path("data")
LOG_PATH = DATA_DIR / "radne_lokacije_log.csv"
MAX_WEEKS_BACK = 2
MAX_WEEKS_FWD = 8
HR_DAYS = ["Ponedjeljak", "Utorak", "Srijeda", "Četvrtak", "Petak"]

st.set_page_config(page_title="Praćenje lokacije rada", page_icon="🗺️", layout="wide")

def read_first_existing(paths):
    for p in paths:
        if p and Path(p).exists():
            return pd.read_csv(p)
    return None

def normalize_employee_columns(df):
    cols = {c.lower(): c for c in df.columns}
    def get(name):
        for key in cols:
            if key.replace(" ", "") == name.replace(" ", "").lower():
                return cols[key]
        return None
    name_c = get("name") or get("ime") or get("imeiprezime")
    dept_c = get("department") or get("odjel") or get("organizacijska jedinica")
    mail_c = get("email") or get("e-mail") or get("mail")
    mgr_c  = get("manager") or get("nadređeni") or get("prvinadređeni")
    dir_c  = get("director") or get("druginadređeni")
    needed = [name_c, dept_c, mail_c]
    if any(x is None for x in needed):
        raise ValueError("CSV mora sadržavati barem Name, Department i eMail stupce.")
    out = df[[name_c, dept_c, mail_c]].copy()
    out.columns = ["Name", "Department", "eMail"]
    out["Manager"] = df[mgr_c] if mgr_c and mgr_c in df.columns else ""
    out["Director"] = df[dir_c] if dir_c and dir_c in df.columns else ""
    return out

def try_import_holidays():
    try:
        import CroatianHolidays as CH
        if hasattr(CH, "HOLIDAYS") and isinstance(CH.HOLIDAYS, dict):
            return lambda d: CH.HOLIDAYS.get(d.strftime("%Y-%m-%d"))
        if hasattr(CH, "get_holiday"):
            return lambda d: CH.get_holiday(d)
    except Exception:
        pass
    return lambda d: None

def get_week_monday(ref: date, offset_weeks: int) -> date:
    weekday = ref.weekday()
    monday = ref - timedelta(days=weekday)
    return monday + timedelta(weeks=offset_weeks)

def iso_week(dt: date) -> int:
    return dt.isocalendar()[1]

def current_cro_date() -> date:
    return date.today()

def load_locations():
    df = read_first_existing(LOKACIJE_PATHS)
    if df is not None and not df.empty:
        col = df.columns[0]
        return [x for x in df[col].dropna().astype(str).tolist() if x.strip()]
    return ["Ured", "Remote", "Na terenu", "Klijent A", "Klijent B"]

def ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)
    if not LOG_PATH.exists():
        pd.DataFrame(columns=["Datum", "Ime i prezime", "Odjel", "Lokacija", "Week", "Month", "Year"]).to_csv(LOG_PATH, index=False)

def load_log():
    ensure_data_dir()
    try:
        return pd.read_csv(LOG_PATH, parse_dates=["Datum"], dayfirst=True)
    except Exception:
        return pd.read_csv(LOG_PATH)

def save_entries(rows):
    ensure_data_dir()
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 0:
        log = pd.read_csv(LOG_PATH)
        new = pd.DataFrame(rows)
        log = pd.concat([log, new], ignore_index=True)
        log.to_csv(LOG_PATH, index=False)
    else:
        pd.DataFrame(rows).to_csv(LOG_PATH, index=False)

def prefill_for_week(log_df, person_name, week_start: date):
    week = iso_week(week_start)
    year = week_start.isocalendar()[0]
    if log_df is None or log_df.empty:
        return {}
    df = log_df.copy()
    if "Datum" in df.columns:
        try:
            df["Datum"] = pd.to_datetime(df["Datum"], errors="coerce", dayfirst=True)
        except Exception:
            pass
    df = df[(df["Ime i prezime"] == person_name) & (df["Year"] == year) & (df["Week"] == week)]
    out = {}
    for _, r in df.iterrows():
        try:
            d = pd.to_datetime(r["Datum"], dayfirst=True).date()
            out[d] = str(r["Lokacija"])
        except Exception:
            continue
    return out

raw_emp = read_first_existing(EMP_CSV_PATHS)
if raw_emp is None:
    st.error("Nije pronađen 'Popis_djelatnika_HR_Sales.csv'. Postavite ga u root repozitorija ili definirajte EMP_CSV_PATH.")
    st.stop()

employees = normalize_employee_columns(raw_emp)
employees["eMail_lc"] = employees["eMail"].str.lower().str.strip()

hol_name = try_import_holidays()
LOCATIONS = load_locations()

# KORAK 1
st.header("👋 Dobrodošli")
today = current_cro_date()
st.write(f"Danas je: **{today.strftime('%d.%m.%Y.')}**")
email = st.text_input("Unesite svoju e-mail adresu", key="email").strip().lower()
if not email:
    st.stop()
row = employees[employees["eMail_lc"] == email]
if row.empty:
    st.error("E-mail nije pronađen u popisu djelatnika.")
    st.stop()
person = row.iloc[0]
full_name = person["Name"]
dept = person["Department"]

# KORAK 2
st.success(f"Pozdrav, **{full_name}** ({dept})!")
st.caption("Unesite lokaciju rada za odabrani tjedan.")

if "week_offset" not in st.session_state:
    st.session_state.week_offset = 1  # sljedeći tjedan

col_a, col_b, col_c, col_d = st.columns([1,1,1,1])
with col_a:
    if st.button("⬅️ Prethodni tjedan", disabled=st.session_state.week_offset <= -MAX_WEEKS_BACK):
        st.session_state.week_offset -= 1
with col_b:
    if st.button("Ovaj tjedan"):
        st.session_state.week_offset = 0
with col_c:
    if st.button("Sljedeći tjedan"):
        st.session_state.week_offset = 1
with col_d:
    if st.button("➡️ Sljedeći ➕", disabled=st.session_state.week_offset >= MAX_WEEKS_FWD):
        st.session_state.week_offset += 1

ref_monday = get_week_monday(today, st.session_state.week_offset)
week_num = iso_week(ref_monday)
week_end = ref_monday + timedelta(days=6)
st.subheader(f"Tjedan {week_num} ({ref_monday.strftime('%d.%m.%Y.')} — {week_end.strftime('%d.%m.%Y.')})")

log_df = load_log()
prefill = prefill_for_week(log_df, full_name, ref_monday)

with st.form("unos_lokacija"):
    entries = []
    st.write("A - Datum, B - Dan, C - Lokacija")

    for i in range(5):  # pon-pet
        day = ref_monday + timedelta(days=i)
        day_name = ["Ponedjeljak","Utorak","Srijeda","Četvrtak","Petak"][i]
        holiday = hol_name(day)

        cols = st.columns([2, 2, 3])
        with cols[0]:
            st.markdown(f"**A - Datum:** {day.strftime('%d.%m.%Y.')}")
        with cols[1]:
            st.markdown(f"**B - Dan:** {day_name}")
        with cols[2]:
            key = f"loc_{day.isoformat()}"
            default_value = prefill.get(day, None)

            if holiday:
                st.text_input("C - Lokacija", value=holiday, key=key, disabled=True, help="Neradni dan prema CroatianHolidays")
                entries.append({
                    "Datum": day.strftime("%d.%m.%Y."),
                    "Ime i prezime": full_name,
                    "Odjel": dept,
                    "Lokacija": holiday,
                    "Week": week_num,
                    "Month": day.month,
                    "Year": day.year,
                })
            else:
                options = ["(odaberi iz popisa)"] + LOCATIONS + ["(upiši ručno)"]
                if default_value and default_value not in options:
                    options.insert(1, default_value)
                sel = st.selectbox("C - Lokacija", options, index=options.index(default_value) if default_value in options else 0, key=key+"_sel")
                value = ""
                if sel == "(upiši ručno)":
                    value = st.text_input("Upišite lokaciju", value=default_value or "", key=key+"_free")
                elif sel != "(odaberi iz popisa)":
                    value = sel
                if isinstance(value, str) and value.strip().lower() == "neradni dan":
                    st.warning("Ne možete unijeti vrijednost 'Neradni dan'.")
                    value = ""
                entries.append({
                    "Datum": day.strftime("%d.%m.%Y."),
                    "Ime i prezime": full_name,
                    "Odjel": dept,
                    "Lokacija": value,
                    "Week": week_num,
                    "Month": day.month,
                    "Year": day.year,
                })

    submitted = st.form_submit_button("💾 Spremi tjedne unose")
    if submitted:
        to_save = [r for r in entries if r["Lokacija"]]
        if not to_save:
            st.info("Nema unosa za spremanje.")
        else:
            save_entries(to_save)
            st.success("Unosi su spremljeni.")

st.markdown("---")
st.subheader("Udio lokacija u tekućoj godini (vaši unosi)")

log = load_log()
if log is not None and not log.empty:
    try:
        log["Datum"] = pd.to_datetime(log["Datum"], dayfirst=True, errors="coerce")
    except Exception:
        pass
    cur_year = today.year
    data_user = log[(log["Ime i prezime"] == full_name) & (log["Year"] == cur_year)]
    if not data_user.empty:
        counts = data_user["Lokacija"].value_counts()
        fig, ax = plt.subplots()
        ax.pie(counts.values, labels=counts.index, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
        st.pyplot(fig)
        st.caption("Graf prikazuje postotke lokacija rada tijekom tekuće godine na temelju spremljenih unosa.")
    else:
        st.info("Nema spremljenih unosa za tekuću godinu.")
else:
    st.info("Još nema podataka u dnevniku.")

st.caption("Podaci se pohranjuju u **data/radne_lokacije_log.csv** (kolone: Datum, Ime i prezime, Odjel, Lokacija, Week, Month, Year).")