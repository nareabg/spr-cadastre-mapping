"""
Step 7 - Integrity checks. The run fails if any check fails.

Also writes output/spr_with_id.csv: the SPR file exactly as received, with
the spr_id surrogate key as the first column.
"""
import pandas as pd

import config
from mapping.db import connect, log
from mapping.normalize import number_key, place_key

STEP = "07_checks"


def run():
    con = connect()
    one = lambda q: con.execute(q).fetchone()[0]
    checks = {}

    raw_n = len(pd.read_csv(config.SPR_FILE, dtype=str, encoding="utf-8-sig", keep_default_na=False, usecols=[0]))
    checks["result has one row per SPR file row"] = one("SELECT count(*) FROM spr_mapping_result") == raw_n
    checks["spr_id is unique and runs 1..N"] = one(
        "SELECT count(DISTINCT spr_id) = count(*) AND min(spr_id) = 1 AND max(spr_id) = count(*) FROM spr_mapping_result")
    checks["spr table equals the raw file (no value changed)"] = one("""
        SELECT count(*) = 0 FROM (SELECT * EXCLUDE (spr_id) FROM spr EXCEPT ALL SELECT * FROM raw_spr)""")
    checks["cadastre table equals the raw file"] = one("""
        SELECT count(*) = 0 FROM (SELECT * FROM cadastre EXCEPT ALL SELECT * FROM raw_cadastre)""")
    checks["cadastre_id is set exactly for mapped rows"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result
        WHERE (match_status = 'mapped') <> (cadastre_id IS NOT NULL)""")
    checks["every mapped/suggested id exists in the cadastre"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result r
        WHERE coalesce(r.cadastre_id, r.suggested_cadastre_id) IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM cadastre c WHERE c.id = coalesce(r.cadastre_id, r.suggested_cadastre_id))""")
    checks["every non-mapped row has an issue category"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result WHERE match_status <> 'mapped' AND issue_category IS NULL""")
    checks["mapped rows have a confidence, others do not"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result
        WHERE (match_status = 'mapped') <> (match_confidence IS NOT NULL)""")

    # re-verify mapped rows from the RAW columns, independent of the pipeline keys
    m = con.execute("""
        SELECT r.spr_id, r.region, r.community, r.house, r.apartment,
               c.region AS c_region, c.community AS c_community, c.settlement AS c_settlement,
               c.house AS c_house, c.building AS c_building
        FROM spr_mapping_result r JOIN cadastre c ON c.id = r.cadastre_id""").df()
    same_region = m["region"].map(place_key) == m["c_region"].map(place_key)
    same_house = m["house"].map(number_key) == m["c_house"].map(number_key)
    same_unit = m["apartment"].map(number_key) == m["c_building"].map(number_key)
    yerevan = m["region"].map(place_key) == place_key("Երևան")
    no_district = m["community"].isna() | (m["community"].fillna("").str.strip() == "")   # nare_rules #6
    same_district = ~yerevan | no_district | (m["community"].map(place_key) == m["c_settlement"].map(place_key))
    checks["mapped: same region (raw values)"] = bool(same_region.all())
    checks["mapped: same house number (raw values)"] = bool(same_house.all())
    checks["mapped: same apartment / building number (raw values)"] = bool(same_unit.all())
    checks["mapped Yerevan rows: same district (raw values)"] = bool(same_district.all())
    # mapped and suggested ids stay inside the settlement / district the SPR row names
    checks["mapped: found in the settlement / district the SPR names"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result r JOIN spr_prepared p USING (spr_id)
        JOIN cadastre_keys k ON k.cadastre_id = r.cadastre_id
        WHERE p.region_k <> k.region_k OR p.community_k <> k.community_k
           OR (p.settlement_k <> k.settlement_k AND p.settlement_k <> '')""")
    checks["mapped without a place in SPR: only Yerevan rows without district (nare_rules #6)"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result r JOIN spr_prepared p USING (spr_id)
        WHERE r.match_status = 'mapped' AND p.geo_note IS NOT NULL AND p.geo_note <> 'district_missing'""")
    checks["Yerevan rows with a district: no suggested id in another district"] = one("""
        SELECT count(*) = 0 FROM spr_mapping_result r JOIN spr_prepared p USING (spr_id)
        JOIN cadastre_keys k ON k.cadastre_id = r.suggested_cadastre_id
        WHERE p.region_k = 'երեվան' AND p.settlement_k <> '' AND p.geo_note IS NULL
          AND k.settlement_k <> p.settlement_k""")

    for name, ok in checks.items():
        log(STEP, ("PASS  " if ok else "FAIL  ") + name)

    shared = one("""SELECT count(*) FROM (SELECT cadastre_id FROM spr_mapping_result
                    WHERE cadastre_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1)""")
    log(STEP, f"info: {shared:,} cadastre ids are the target of more than one SPR row "
              f"(the same address written differently in SPR)")

    con.execute("SELECT * FROM spr ORDER BY spr_id").df().to_csv(
        config.OUTPUT_DIR / "spr_with_id.csv", index=False, encoding="utf-8-sig")
    con.close()
    assert all(checks.values()), "An integrity check failed - see the FAIL lines above."


if __name__ == "__main__":
    run()
