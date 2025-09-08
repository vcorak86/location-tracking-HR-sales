
import pandas as pd, pyarrow as pa, pyarrow.parquet as pq
from utils_tracker import dedupe_last_then_sort_desc, apply_canonical_fields

df = pd.read_csv('data/Tracker.csv', sep=None, engine='python')
df = apply_canonical_fields(df, source='ci')
df = dedupe_last_then_sort_desc(df)
table = pa.Table.from_pandas(df)
pq.write_table(table, 'data/Tracker.parquet')
print('Wrote data/Tracker.parquet')
