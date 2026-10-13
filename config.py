"""Paths and settings. Change only this file to point the pipeline at new inputs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Inputs (read-only). The cadastre can be .xlsx, .csv or .parquet.
CADASTRE_FILE = ROOT / "data" / "input" / "cadastre_all_data.xlsx"
SPR_FILE = ROOT / "data" / "input" / "spr_all_unmapped_addresses.csv"

# Working database (all intermediate tables) and outputs.
DB_FILE = ROOT / "work" / "mapping.duckdb"
OUTPUT_DIR = ROOT / "output"

# Excel's sheet limit. A cadastre export with exactly this many data rows was
# probably cut off; the pipeline reports it as a cadastre issue.
EXCEL_MAX_DATA_ROWS = 1_048_575
