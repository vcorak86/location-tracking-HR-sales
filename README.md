# Location Tracking HR Sales (v12.2)

- `app.py`: Streamlit aplikacija (Debug panel uključen)
- `utils_tracker.py`: pomoćne funkcije (normalizacija, last-wins, DESC sortiranje, heuristike)
- `data/`: CSV datoteke (Tracker, Popis djelatnika, Locations_normalized, CroatianHolidays)
- `.streamlit/secrets.example.toml`: primjer konfiguracije (kopiraj u `secrets.toml` na Streamlit Cloudu i popuni)
- `.github/workflows/tests.yml`: CI smoke test

## Napomene
- Spremanje tjednih unosa upisuje **svih 5 dana** (ako su zadani) i radi canonical mapiranje (`location_id`/`location_name`).
- Uvijek se primjenjuje **last-wins** po `(Ime i prezime, date_iso)` i zapis je **globalno DESC** po datumu.
- Admin **Debug panel** omogućuje pregled payload-a prije snimanja, testni merge bez snimanja i status zadnjih GitHub poziva.
