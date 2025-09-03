# Praćenje lokacije rada (Streamlit) — v4

Ova verzija koristi **CroatianHolidays.csv** (umjesto `.py` modula) za neradne dane.

## Očekivani format CroatianHolidays.csv
Separator: `;` (točka-zarez). Preporučene kolone:
- `Datum` — npr. `01.01.2025.`
- `Državni praznik` — npr. `Nova godina`
- `Dan` — informativno (nije obavezno)

App je tolerantan na nazive kolona (traži po heuristici).

## Pokretanje
```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```