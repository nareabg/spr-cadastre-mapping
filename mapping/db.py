"""Small helpers around the working DuckDB database."""
import duckdb
import pandas as pd

import config


def connect():
    config.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(config.DB_FILE))


def save_df(con, name: str, df: pd.DataFrame):
    con.register("_tmp_df", df)
    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM _tmp_df")
    con.unregister("_tmp_df")


def log(step: str, msg: str):
    print(f"[{step}] {msg}", flush=True)
