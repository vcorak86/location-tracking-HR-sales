
import pandas as pd, sys
from utils_tracker import validate_tracker_schema

def main():
    df=pd.read_csv('data/Tracker.csv', sep=None, engine='python')
    issues=validate_tracker_schema(df)
    if issues:
        print("Schema issues detected:\n- " + "\n- ".join(issues))
        sys.exit(1)
    print("Schema OK.")
    return 0

if __name__=='__main__':
    raise SystemExit(main())
