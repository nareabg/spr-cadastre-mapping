"""
Builds the dashboard's data files from the pipeline's outputs and inputs.

Reads (never changes):
  output/spr_mapping_result.parquet (or .csv)   final result, one row per SPR row
  output/spr_with_id.csv                        SPR as received + spr_id (earlier match_type)
  output/issues_for_cadastre.xlsx               sheet cadastre_only_checks
  data/input/cadastre_all_data*                 the cadastre part(s)

Writes dashboard/data/*.parquet. The app calls this automatically when the
files are missing or older than the outputs; you can also run it by hand:
    python dashboard/prepare_data.py
"""
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
OUT = config.OUTPUT_DIR

SOURCES = {
    "result": [OUT / "spr_mapping_result.parquet", OUT / "spr_mapping_result.csv"],
    "spr_with_id": [OUT / "spr_with_id.csv"],
    "cad_issues": [OUT / "issues_for_cadastre.xlsx"],
}
TARGETS = ["spr_result.parquet", "cadastre.parquet", "cadastre_issues.parquet"]


def _first_existing(paths):
    for p in paths:
        if p.exists():
            return p
    raise FileNotFoundError(f"Missing {paths[0].name} in {paths[0].parent}. Run the pipeline first (run_all.py).")


def needs_rebuild() -> bool:
    if not all((DATA / t).exists() for t in TARGETS):
        return True
    built = min((DATA / t).stat().st_mtime for t in TARGETS)
    inputs = [_first_existing(v) for v in SOURCES.values()] + list(config.CADASTRE_FILES)
    return any(p.stat().st_mtime > built for p in inputs)


def _read_table(path):
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".xlsx":
        return pd.read_excel(path, dtype=str, engine="calamine")
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False, na_values=[""])


def build(log=print):
    DATA.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    log("Reading the mapping result ...")
    res = _read_table(_first_existing(SOURCES["result"]))
    spr = pd.read_csv(_first_existing(SOURCES["spr_with_id"]), dtype=str, encoding="utf-8-sig",
                      usecols=["spr_id", "match_type", "cad_candidate_ids"])
    spr = spr.rename(columns={"match_type": "earlier_match_type", "cad_candidate_ids": "earlier_candidate_ids"})
    res["spr_id"] = res["spr_id"].astype("int64")
    spr["spr_id"] = spr["spr_id"].astype("int64")
    res = res.merge(spr, on="spr_id", how="left")
    res["similarity_score"] = pd.to_numeric(res["similarity_score"], errors="coerce")
    res["data_completeness"] = pd.to_numeric(res["data_completeness"], errors="coerce")
    res.to_parquet(DATA / "spr_result.parquet", index=False)

    log("Reading the cadastre data issues ...")
    iss = pd.read_excel(_first_existing(SOURCES["cad_issues"]), sheet_name="cadastre_only_checks",
                        dtype=str, engine="calamine")
    iss = iss.rename(columns={"id": "cadastre_id"})
    iss.to_parquet(DATA / "cadastre_issues.parquet", index=False)

    log("Reading the cadastre file(s) ...")
    parts = [_read_table(p).astype("string") for p in config.CADASTRE_FILES]
    cad = pd.concat(parts, ignore_index=True).drop_duplicates()
    cad = cad.rename(columns={"id": "cadastre_id"})
    con.register("cad", cad)
    con.register("iss", iss)
    con.register("res", res)
    cad = con.execute("""
        WITH i AS (SELECT cadastre_id, string_agg(DISTINCT issue_code, ', ' ORDER BY issue_code) AS cadastre_issues
                   FROM iss GROUP BY 1),
             m AS (SELECT cadastre_id, count(*) AS spr_rows_mapped FROM res
                   WHERE cadastre_id IS NOT NULL GROUP BY 1)
        SELECT cad.*, i.cadastre_issues, coalesce(m.spr_rows_mapped, 0) AS spr_rows_mapped
        FROM cad LEFT JOIN i USING (cadastre_id) LEFT JOIN m USING (cadastre_id)
    """).df()
    cad.to_parquet(DATA / "cadastre.parquet", index=False)
    log(f"Done: {len(res):,} SPR rows, {len(cad):,} cadastre rows, {len(iss):,} cadastre issue rows.")


if __name__ == "__main__":
    build()
