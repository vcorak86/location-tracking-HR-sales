# Praćenje lokacije rada (Streamlit) — v7

**Što je novo:**
- GitHub path je **`data/Tracker.csv`**
- CSV separator za GitHub sync je **`;`**

Aplikacija radi „GitHub-first“: čita/piše `data/Tracker.csv` u repozitoriju (ako je konfiguriran *secrets*), a lokalno vodi keš `data/Tracker.local.csv`.

## Obavezne datoteke
- `app.py`
- `Popis_djelatnika_HR_Sales.csv`  (separator `;`)
- `Locations.csv`                  (prva kolona; app čisti vrijednosti)
- `CroatianHolidays.csv`          (separator `;` s kolonom `Datum` i nazivom praznika)
- **`data/Tracker.csv`**          ← tvoja povijesna datoteka (preporuka: dodaj u repo)

## Streamlit secrets (GitHub)
```toml
[GITHUB]
token = "ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXX"
repo = "ORG_ILI_USER/IME_REPOA"
branch = "main"
path = "data/Tracker.csv"     # v7 default
committer_name = "Streamlit Bot"
committer_email = "bot@example.com"
csv_sep = ";"                 # v7 default
```

## Pokretanje
```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows
# .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
