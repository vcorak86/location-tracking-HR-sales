
# Location Tracking HR (v10.3)

- UX/UI: Kopiraj prošli tjedan, Reset, Undo, zaključavanje prošlih tjedana.
- Data model: canonical polja u Tracker.csv (`date_iso`, `record_id`, `created_at`, `updated_at`, `source`, `version`), last-wins strože.
- Locations_normalized.csv za tipizaciju lokacija.
- Perf: CI gradi Parquet; app preferira Parquet za admin analitiku.
- CI: lint, type-check, tests s coverage, schema check, nightly.
- Healthcheck u adminu (rate limit, scope, GET/PUT).
- Telemetry (Sentry) opcionalno + **Sentry test gumbi**.

## Pokretanje
```
pip install -r requirements.txt
streamlit run app.py
```

## Novosti v10.3
- Dodan **Sentry test** (poruka + simulirana greška) u Admin portalu.
- Dodan **About** (ℹ️) popover s verzijom, SHA i build timestampom.
- Deprecated pozivi očišćeni (`experimental_rerun` -> `st.rerun`), prazni labele uklonjene.
