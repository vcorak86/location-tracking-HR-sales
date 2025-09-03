# Praćenje lokacije rada (Streamlit)

Aplikacija omogućuje unos lokacije rada po danima (pon–pet) uz blokadu neradnih dana iz `CroatianHolidays.py` i pie chart udjela lokacija u tekućoj godini.

## Pokretanje lokalno
```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows
# .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Potrebne datoteke u rootu
- `app.py`
- `Popis_djelatnika_HR_Sales.csv` (separator `;`)
- `Locations.csv` (CP-1250; kolona `LOCATIONS`)
- `CroatianHolidays.py` (klasa `CroatianHolidays` s metodom `getHolidays(godina)`)
- `data/` (prazno; kreira se `radne_lokacije_log.csv`)

## Log format
`data/radne_lokacije_log.csv` s kolonama: `Datum | Ime i prezime | Odjel | Lokacija | Week | Month | Year`.