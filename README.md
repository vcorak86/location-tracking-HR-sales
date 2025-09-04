# v8.3.2 — Minimalni testovi + utils modul
## Što je dodano
- `utils_tracker.py` s funkcijama: `normalize_columns`, `parse_date_flexible`, `with_parsed_date`, `dedupe_last_then_sort_desc`, `is_remote_value`.
- `app.py` sada uvozi funkcije iz utils modula (nema promjena za korisnika).
- `tests/` s `pytest` testovima:
  - `test_dateparse.py` — provjerava različite formate datuma.
  - `test_dedupe.py` — provjerava last-wins i sortiranje DESC.
## Kako pokrenuti testove
```bash
pip install -r requirements.txt
pytest -q
```
