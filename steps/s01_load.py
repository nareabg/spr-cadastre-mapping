"""
Step 1 - Load the two inputs, untouched.

  raw_cadastre  : cadastre file as is (every column text)
  raw_spr       : SPR file as is (every column text)
  spr           : raw_spr + spr_id, an identity surrogate key 1..N in file order.
                  Use spr_id to find any row in every output.

match_type / cad_candidate_ids come from an earlier mapping by someone else.
They are kept in spr for reference only and are never read by this pipeline.
"""
import pandas as pd

import config
from mapping.db import connect, save_df, log

STEP = "01_load"


def read_any(path):
    p = str(path).lower()
    if p.endswith(".xlsx"):
        return pd.read_excel(path, dtype=str, engine="calamine")
    if p.endswith(".parquet"):
        return pd.read_parquet(path).astype("string")
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False, na_values=[""])


def run():
    con = connect()
    cad = read_any(config.CADASTRE_FILE)
    spr = read_any(config.SPR_FILE)
    # empty strings -> NULL, so "blank" means one thing everywhere
    cad = cad.replace({"": None})
    spr = spr.replace({"": None})
    save_df(con, "raw_cadastre", cad)
    save_df(con, "raw_spr", spr)

    # identity surrogate key in file order (DuckDB keeps insertion order)
    con.execute("""
        CREATE OR REPLACE TABLE spr AS
        SELECT CAST(row_number() OVER () AS BIGINT) AS spr_id, * FROM raw_spr
    """)
    con.execute("ALTER TABLE spr ADD PRIMARY KEY (spr_id)")
    con.execute("CREATE OR REPLACE TABLE cadastre AS SELECT * FROM raw_cadastre")

    n_cad, n_spr = len(cad), len(spr)
    log(STEP, f"cadastre rows: {n_cad:,}   spr rows: {n_spr:,}")
    if n_cad == config.EXCEL_MAX_DATA_ROWS:
        log(STEP, "WARNING: cadastre has exactly the Excel row limit; the export is probably truncated")
    con.close()


if __name__ == "__main__":
    run()
