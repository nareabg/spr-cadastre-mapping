"""Runs the whole pipeline in order. Each step can also be run alone:
    python -m steps.s03_prepare_spr
"""
import time
from steps import s01_load, s02_cadastre, s03_prepare_spr, s04_match_streets, s05_match_addresses, s06_export, s07_checks

STEPS = [s01_load, s02_cadastre, s03_prepare_spr, s04_match_streets, s05_match_addresses, s06_export, s07_checks]

if __name__ == "__main__":
    t0 = time.time()
    for step in STEPS:
        t = time.time()
        step.run()
        print(f"    done in {time.time() - t:,.0f}s")
    print(f"Pipeline finished in {time.time() - t0:,.0f}s")
