
# Location Tracking HR (v10 clean)

- UX/UI: Kopiraj prošli tjedan, Reset, Undo, napomene za "drugo", zaključavanje prošlih tjedana.
- Data model: canonical polja u Tracker.csv (`date_iso`, `record_id`, `created_at`, `updated_at`, `source`, `version`), last-wins strože.
- Locations_normalized.csv za tipizaciju lokacija.
- Perf: CI gradi Parquet; app preferira Parquet za admin analitiku.
- CI: lint, type-check, tests s coverage, schema check, nightly.
- Healthcheck u adminu (rate limit, scope, GET/PUT).
- Telemetry (Sentry) opcionalno.

## Pokretanje
```
pip install -r requirements.txt
streamlit run app.py
```
