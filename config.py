"""Paths and settings. Change only this file to point the pipeline at new inputs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

INPUT_DIR = ROOT / "data" / "input"

# Inputs (read-only). The cadastre may come in one file or in several parts
# (cadastre_all_data.xlsx, Cadastre_all_data_part1.xlsx, ..._part2.xlsx ...):
# every .xlsx / .csv / .parquet in data/input whose name starts with
# "cadastre_all_data" (any case) is read and stacked, in name order.
CADASTRE_FILES = sorted(
    p for p in INPUT_DIR.glob("*")
    if p.name.lower().startswith("cadastre_all_data") and p.suffix.lower() in (".xlsx", ".csv", ".parquet")
)
SPR_FILE = INPUT_DIR / "spr_all_unmapped_addresses.csv"

# Working database (all intermediate tables) and outputs.
DB_FILE = ROOT / "work" / "mapping.duckdb"
OUTPUT_DIR = ROOT / "output"

# Excel's sheet limit. A cadastre file (or part) with exactly this many data
# rows was probably cut off; the pipeline warns and records it.
EXCEL_MAX_DATA_ROWS = 1_048_575
