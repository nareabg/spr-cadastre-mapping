"""
Step 1 - Load the two inputs, untouched.

  raw_cadastre  : cadastre file(s) as is (every column text). Several parts
                  are stacked; rows repeated across parts (same id, same
                  values) are kept once; the same id with DIFFERENT values
                  stops the run.
  cadastre_parts: one row per cadastre file: rows, ids, Excel-limit flag
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
    if not config.CADASTRE_FILES:
        raise FileNotFoundError(f"No cadastre file found in {config.INPUT_DIR} (name must start with cadastre_all_data)")
    parts, facts = [], []
    for path in config.CADASTRE_FILES:
        part = read_any(path)
        facts.append({"file": path.name, "rows": len(part), "first_id": part["id"].iloc[0],
                      "last_id": part["id"].iloc[-1],
                      "at_excel_limit": len(part) == config.EXCEL_MAX_DATA_ROWS})
        log(STEP, f"cadastre part {path.name}: {len(part):,} rows")
        parts.append(part)
    columns = list(parts[0].columns)
    for path, part in zip(config.CADASTRE_FILES, parts):
        if list(part.columns) != columns:
            raise ValueError(f"{path.name} has different columns than {config.CADASTRE_FILES[0].name}")
    cad = pd.concat(parts, ignore_index=True)
    n_stacked = len(cad)
    cad = cad.drop_duplicates()                       # identical rows repeated across parts
    if cad["id"].duplicated().any():
        bad = cad.loc[cad["id"].duplicated(keep=False), "id"].unique()[:10]
        raise ValueError(f"Cadastre ids appear with different values in the parts, e.g. {list(bad)}")
    if n_stacked != len(cad):
        log(STEP, f"{n_stacked - len(cad):,} cadastre rows were exact repeats (same id, same values); kept once")
    spr = read_any(config.SPR_FILE)
    # empty strings -> NULL, so "blank" means one thing everywhere
    cad = cad.replace({"": None})
    spr = spr.replace({"": None})
    save_df(con, "raw_cadastre", cad)
    save_df(con, "cadastre_parts", pd.DataFrame(facts))
    save_df(con, "raw_spr", spr)

    # identity surrogate key in file order (DuckDB keeps insertion order)
    con.execute("""
        CREATE OR REPLACE TABLE spr AS
        SELECT CAST(row_number() OVER () AS BIGINT) AS spr_id, * FROM raw_spr
    """)
    con.execute("ALTER TABLE spr ADD PRIMARY KEY (spr_id)")
    con.execute("CREATE OR REPLACE TABLE cadastre AS SELECT * FROM raw_cadastre")

    log(STEP, f"cadastre rows: {len(cad):,} (from {len(parts)} file(s))   spr rows: {len(spr):,}")
    for f in facts:
        if f["at_excel_limit"]:
            log(STEP, f"WARNING: {f['file']} has exactly the Excel row limit; that part is probably cut off")
    con.close()


if __name__ == "__main__":
    run()
