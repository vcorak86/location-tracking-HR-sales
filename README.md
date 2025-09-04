# v9 — SHA badge + CI tests
- Header sada prikazuje **badge** s kraćim SHA za `data/Tracker.csv` (npr. `@ a1b2c3`).
- Gumb **🔔 Provjeri nove zapise** pokreće provjeru i refresh.
- Dodan GitHub Actions workflow **tests.yml** (pytest na push/PR) + zadržan **normalize-tracker.yml**.
- Sve ostalo (DESC + last-wins, admin portal, exporti, KPI, hover praznici, spinneri, progress barovi) ostaje.
