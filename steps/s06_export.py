"""
Step 6 - One final row per SPR address, issue categories, and the review files.

Table spr_mapping_result (one row per spr_id, same count as the SPR file):
  cadastre_id        set ONLY when match_status = mapped
  match_status       mapped | suggested | ambiguous | not_found | out_of_scope
  match_confidence   high | medium            (mapped rows only)
  issue_category     why the row is not mapped (see ISSUES below)
  issue_owner        CADASTRE | SPR | REVIEW  (who should look at it)
  issue_detail       the specific evidence (candidate ids, names, counts)
  suggested_cadastre_id / candidate_cadastre_ids   for the reviewer, never final

A row is mapped only when the address is found in the place the SPR row
names (its settlement / Yerevan district). Found elsewhere, or found by a
wider search because SPR gives no settlement / district -> suggested.

high confidence = street found by exact / alias / double_letter / word_form,
read as written (or as neighbourhood + street), exactly one cadastre id,
and its object type agrees with the SPR house type. Every other mapped row
is medium.
"""
import pandas as pd

import config
from mapping.db import connect, log
from mapping.street_matcher import STRONG_RULES, STRONG_READINGS

STEP = "06_export"

# code: (match_status, owner, meaning)
ISSUES = {
    "SPR_FOREIGN_ADDRESS": ("out_of_scope", "SPR", "Region is not one of the 11 cadastre regions (another country, ԼՂՀ, or a code)"),
    "SPR_REGION_MISSING": ("out_of_scope", "SPR", "Region is blank"),
    "GEO_COMMUNITY_NOT_IN_CADASTRE": ("not_found", "REVIEW", "Community name does not exist in the cadastre for that region"),
    "GEO_SETTLEMENT_NOT_IN_CADASTRE": ("not_found", "REVIEW", "Settlement does not exist under that community in the cadastre"),
    "SPR_NO_STREET": ("not_found", "SPR", "Street text is blank or holds only a house word (ՏՆԱԿ, ՏՈՒՆ6)"),
    "SPR_STREET_TEXT_UNPARSED": ("not_found", "SPR", "Street text holds extra parts that change the address (2 streets, lane without number, institution name)"),
    "STREET_NOT_FOUND": ("not_found", "REVIEW", "No cadastre street with this name in the place (old name, typo beyond rules, or missing in cadastre)"),
    "STREET_TYPE_MISMATCH": ("not_found", "REVIEW", "Street name found, but SPR type (Փ., ՆՐԲ. ...) differs from the cadastre type"),
    "STREET_AMBIGUOUS": ("ambiguous", "REVIEW", "Several different cadastre streets fit the SPR street text"),
    "STREET_SIMILAR_ONLY": ("suggested", "REVIEW", "Only a similar spelling found (fuzzy 80-90, or too close to another street)"),
    "STREET_STREETLESS_SUGGESTED": ("suggested", "REVIEW", "SPR has no street; settlement has street-less cadastre addresses but also named streets"),
    "SPR_HOUSE_MISSING": ("not_found", "SPR", "House number is blank"),
    "SPR_STREET_NUMBER_CONFLICT": ("not_found", "SPR", "A number written in the street text differs from the house number"),
    "CAD_HOUSE_NOT_FOUND": ("not_found", "CADASTRE", "Street exists in the cadastre, this house number does not"),
    "CAD_APARTMENT_NOT_FOUND": ("not_found", "CADASTRE", "House exists with apartments, this apartment number does not"),
    "CAD_HOUSE_HAS_NO_UNITS": ("not_found", "REVIEW", "SPR gives an apartment; the cadastre house has no apartments at all"),
    "SPR_APARTMENT_MISSING": ("not_found", "SPR", "Cadastre house is split into apartments; SPR gives no apartment"),
    "CAD_DUPLICATE_ADDRESS": ("ambiguous", "CADASTRE", "Several cadastre ids with identical address and object type"),
    "ADDRESS_SEVERAL_OBJECTS": ("ambiguous", "REVIEW", "Several cadastre ids at this address (house, land plot ...); SPR house type does not decide"),
    "OBJECT_TYPE_CONFLICT": ("suggested", "REVIEW", "The only cadastre id is a different object (land plot for a house, garage for an apartment)"),
    "ADDRESS_FOUND_ON_SUGGESTED_STREET": ("suggested", "REVIEW", "Street only suggested or type differs, but this exact house/apartment exists on it"),
    "STREET_FOUND_IN_OTHER_DISTRICT": ("suggested", "REVIEW", "Yerevan: street not in the district the SPR names; the address exists in another district"),
    "GEO_PLACE_MISSING_FOUND_IN_WIDER_AREA": ("suggested", "REVIEW", "SPR gives no district / settlement; the address was found once in the whole city / community"),
}

ADDRESS_TO_ISSUE = {
    "house_missing_in_spr": "SPR_HOUSE_MISSING",
    "street_text_number_conflicts_house": "SPR_STREET_NUMBER_CONFLICT",
    "house_not_found": "CAD_HOUSE_NOT_FOUND",
    "apartment_not_found": "CAD_APARTMENT_NOT_FOUND",
    "house_has_no_units_in_cadastre": "CAD_HOUSE_HAS_NO_UNITS",
    "apartment_missing_in_spr": "SPR_APARTMENT_MISSING",
    "cadastre_duplicate": "CAD_DUPLICATE_ADDRESS",
    "several_objects": "ADDRESS_SEVERAL_OBJECTS",
    "object_type_conflict": "OBJECT_TYPE_CONFLICT",
}


def build_result(con):
    con.execute("CREATE OR REPLACE TEMP TABLE issue_ref AS SELECT * FROM _issue_ref")
    strong_rules = ", ".join(f"'{r}'" for r in STRONG_RULES)
    strong_reads = ", ".join(f"'{r}'" for r in STRONG_READINGS)
    addr_case = "\n".join(f"WHEN '{k}' THEN '{v}'" for k, v in ADDRESS_TO_ISSUE.items())
    con.execute(f"""
    CREATE OR REPLACE TABLE spr_mapping_result AS
    WITH j AS (
        SELECT s.spr_id, s.region, s.community, s.settlement, s.street, s.house, s.house_type, s.apartment,
               p.geo_rule, p.geo_status, p.geo_note, p.geo_hint, p.data_completeness,
               m.street_status, m.street_uid, m.candidate_uids, m.match_method, m.text_reading, m.searched_in,
               m.scope_widened, m.type_check, m.similarity_score, m.parsed_street,
               a.address_status, a.cadastre_id, a.suggested_cadastre_id, a.address_candidates,
               a.address_pick, a.object_type_check, a.n_house_rows, a.street_confirmed
        FROM spr s
        JOIN spr_prepared p USING (spr_id)
        LEFT JOIN spr_street_match m USING (spr_id)
        LEFT JOIN spr_address_match a USING (spr_id)
    ),
    c AS (
        SELECT *,
          CASE
            WHEN geo_status = 'foreign_or_unknown_region'  THEN 'SPR_FOREIGN_ADDRESS'
            WHEN geo_status = 'region_missing'             THEN 'SPR_REGION_MISSING'
            WHEN geo_status = 'community_not_in_cadastre'  THEN 'GEO_COMMUNITY_NOT_IN_CADASTRE'
            WHEN geo_status = 'settlement_not_in_cadastre' THEN 'GEO_SETTLEMENT_NOT_IN_CADASTRE'
            WHEN street_status = 'no_street_in_spr'        THEN 'SPR_NO_STREET'
            WHEN street_status = 'no_readings'             THEN 'SPR_STREET_TEXT_UNPARSED'
            WHEN street_status = 'not_found'               THEN 'STREET_NOT_FOUND'
            WHEN street_status = 'ambiguous'               THEN 'STREET_AMBIGUOUS'
            WHEN street_status = 'found' AND address_status = 'mapped' AND scope_widened
                 THEN 'STREET_FOUND_IN_OTHER_DISTRICT'
            WHEN street_status = 'found' AND address_status = 'mapped' AND geo_note IS NOT NULL
                 THEN 'GEO_PLACE_MISSING_FOUND_IN_WIDER_AREA'
            WHEN street_status = 'found' AND address_status = 'mapped' THEN NULL
            WHEN street_status = 'found' THEN CASE address_status {addr_case} END
            WHEN address_status = 'mapped' THEN 'ADDRESS_FOUND_ON_SUGGESTED_STREET'
            WHEN street_status = 'type_mismatch'           THEN 'STREET_TYPE_MISMATCH'
            WHEN street_status = 'suggested' AND match_method = 'no_street' THEN 'STREET_STREETLESS_SUGGESTED'
            WHEN street_status = 'suggested'               THEN 'STREET_SIMILAR_ONLY'
          END AS issue_category
        FROM j
    )
    SELECT
        c.spr_id, c.region, c.community, c.settlement, c.street, c.house, c.house_type, c.apartment,
        CASE WHEN c.issue_category IS NULL THEN c.cadastre_id END                 AS cadastre_id,
        CASE WHEN c.issue_category IS NULL THEN 'mapped' ELSE r.match_status END  AS match_status,
        CASE WHEN c.issue_category IS NOT NULL THEN NULL
             WHEN c.match_method IN ({strong_rules}) AND c.text_reading IN ({strong_reads})
                  AND c.address_pick = 'single' AND c.object_type_check = 'same' THEN 'high'
             ELSE 'medium' END                                                     AS match_confidence,
        c.issue_category, r.owner AS issue_owner,
        CASE WHEN c.issue_category IN ('STREET_FOUND_IN_OTHER_DISTRICT', 'GEO_PLACE_MISSING_FOUND_IN_WIDER_AREA')
             THEN c.cadastre_id ELSE c.suggested_cadastre_id END                   AS suggested_cadastre_id,
        CASE WHEN c.issue_category IN ('CAD_DUPLICATE_ADDRESS', 'ADDRESS_SEVERAL_OBJECTS') THEN c.address_candidates END
                                                                                   AS candidate_cadastre_ids,
        c.geo_rule, c.geo_note, c.geo_hint,
        c.street_status, c.match_method AS street_match_method, c.text_reading, c.searched_in, c.scope_widened,
        c.type_check, round(c.similarity_score, 1) AS similarity_score, c.parsed_street,
        c.street_uid, c.candidate_uids AS candidate_street_uids,
        c.address_status, c.address_pick, c.object_type_check, c.data_completeness
    FROM c LEFT JOIN issue_ref r ON r.code = c.issue_category
    ORDER BY c.spr_id
    """)

    # human-readable detail per category
    con.execute("""
    CREATE OR REPLACE TABLE spr_mapping_result AS
    WITH cand_names AS (
        SELECT r.spr_id,
               string_agg(cs.settlement || ': ' || coalesce(cs.street_name, '∅') || ' ' || coalesce(cs.street_type, '')
                          || coalesce(' / ' || cs.sub_street_name || ' ' || coalesce(cs.sub_street_type, ''), ''),
                          ' ; ' ORDER BY cs.street_uid) AS names
        FROM spr_mapping_result r,
             unnest(string_split(r.candidate_street_uids, ',')) AS u(uid)
        JOIN cadastre_streets cs ON cs.street_uid = CAST(u.uid AS BIGINT)
        WHERE r.issue_category IN ('STREET_AMBIGUOUS', 'STREET_TYPE_MISMATCH', 'STREET_SIMILAR_ONLY',
                                   'ADDRESS_FOUND_ON_SUGGESTED_STREET', 'STREET_STREETLESS_SUGGESTED')
        GROUP BY r.spr_id
    ),
    street_names AS (
        SELECT r.spr_id, cs.street_name || ' ' || coalesce(cs.street_type, '')
               || coalesce(' / ' || cs.sub_street_name || ' ' || coalesce(cs.sub_street_type, ''), '') AS name,
               cs.n_addresses
        FROM spr_mapping_result r JOIN cadastre_streets cs ON cs.street_uid = r.street_uid
    )
    SELECT r.*,
      CASE r.issue_category
        WHEN 'GEO_COMMUNITY_NOT_IN_CADASTRE'  THEN coalesce(r.geo_hint, 'no similar place found')
        WHEN 'GEO_SETTLEMENT_NOT_IN_CADASTRE' THEN coalesce(r.geo_hint, 'no similar place found')
        WHEN 'STREET_NOT_FOUND'     THEN 'searched for: ' || coalesce(r.parsed_street, '')
        WHEN 'STREET_TYPE_MISMATCH' THEN 'SPR: ' || coalesce(r.parsed_street, '') || '  ->  cadastre: ' || cn.names
        WHEN 'STREET_AMBIGUOUS'     THEN 'candidates (' || r.street_match_method || '): ' || cn.names
        WHEN 'STREET_SIMILAR_ONLY'  THEN 'closest (score ' || r.similarity_score || '): ' || cn.names
        WHEN 'STREET_STREETLESS_SUGGESTED' THEN 'street-less cadastre addresses in ' || coalesce(r.settlement, '')
        WHEN 'STREET_FOUND_IN_OTHER_DISTRICT' THEN 'found in district ' || coalesce(cs2.settlement, '') || ': ' || sn.name
        WHEN 'GEO_PLACE_MISSING_FOUND_IN_WIDER_AREA' THEN 'found in ' || coalesce(cs2.settlement, '') || ': ' || sn.name
        WHEN 'ADDRESS_FOUND_ON_SUGGESTED_STREET' THEN 'street ' || r.street_status || ' (' || r.street_match_method || '): ' || cn.names
        WHEN 'CAD_HOUSE_NOT_FOUND'  THEN 'street ' || sn.name || ' has ' || sn.n_addresses || ' cadastre addresses; house ' || coalesce(r.house, '') || ' is not one of them'
        WHEN 'CAD_APARTMENT_NOT_FOUND' THEN 'house ' || coalesce(r.house, '') || ' on ' || sn.name || ' exists; apartment ' || coalesce(r.apartment, '') || ' does not'
        WHEN 'CAD_HOUSE_HAS_NO_UNITS' THEN 'house ' || coalesce(r.house, '') || ' on ' || sn.name || ' has no apartments in the cadastre'
        WHEN 'SPR_APARTMENT_MISSING' THEN 'house ' || coalesce(r.house, '') || ' on ' || sn.name || ' exists only as separate units'
        WHEN 'CAD_DUPLICATE_ADDRESS' THEN 'identical cadastre ids: ' || r.candidate_cadastre_ids
        WHEN 'ADDRESS_SEVERAL_OBJECTS' THEN 'cadastre ids at this address: ' || r.candidate_cadastre_ids
        WHEN 'OBJECT_TYPE_CONFLICT' THEN 'SPR house type ' || coalesce(r.house_type, '∅') || ' vs cadastre object of id ' || r.suggested_cadastre_id
        WHEN 'SPR_STREET_NUMBER_CONFLICT' THEN 'street text: ' || r.street || ' ; house: ' || coalesce(r.house, '')
      END AS issue_detail
    FROM spr_mapping_result r
    LEFT JOIN cand_names cn USING (spr_id)
    LEFT JOIN street_names sn USING (spr_id)
    LEFT JOIN cadastre_streets cs2 ON cs2.street_uid = r.street_uid
    ORDER BY r.spr_id
    """)


def export(con):
    out = config.OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    # 1. full row-level result, joined to the mapped cadastre row for eyeballing
    full = con.execute("""
        SELECT r.spr_id, r.region, r.community, r.settlement, r.street, r.house, r.house_type, r.apartment,
               r.cadastre_id, r.match_status, r.match_confidence, r.issue_category, r.issue_owner, r.issue_detail,
               r.suggested_cadastre_id, r.candidate_cadastre_ids,
               r.street_match_method, r.text_reading, r.searched_in, r.type_check, r.similarity_score,
               r.address_pick, r.object_type_check, r.geo_rule, r.data_completeness,
               c.region AS cad_region, c.community AS cad_community, c.settlement AS cad_settlement,
               c.street_name AS cad_street_name, c.street_type AS cad_street_type,
               c.sub_street_name AS cad_sub_street_name, c.sub_street_type AS cad_sub_street_type,
               c.house AS cad_house, c.house_type AS cad_house_type,
               c.building AS cad_building, c.building_type AS cad_building_type
        FROM spr_mapping_result r
        LEFT JOIN cadastre c ON c.id = coalesce(r.cadastre_id, r.suggested_cadastre_id)
        ORDER BY r.spr_id
    """).df()
    full.to_csv(out / "spr_mapping_result.csv", index=False, encoding="utf-8-sig")
    full.to_parquet(out / "spr_mapping_result.parquet", index=False)

    # 2. summary workbook
    status = con.execute("""
        SELECT match_status, match_confidence, count(*) AS rows,
               round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM spr_mapping_result GROUP BY ALL ORDER BY rows DESC""").df()
    cats = con.execute("""
        SELECT r.issue_category, i.match_status, i.owner, i.meaning, count(*) AS rows,
               round(100.0 * count(*) / (SELECT count(*) FROM spr_mapping_result), 2) AS pct_of_all
        FROM spr_mapping_result r JOIN issue_ref i ON i.code = r.issue_category
        GROUP BY ALL ORDER BY rows DESC""").df()
    methods = con.execute("""
        SELECT street_match_method, text_reading, searched_in, type_check, address_pick, object_type_check,
               match_confidence, count(*) AS rows
        FROM spr_mapping_result WHERE match_status = 'mapped' GROUP BY ALL ORDER BY rows DESC""").df()
    cad_issues = con.execute("""
        SELECT issue_code, count(*) AS cadastre_ids FROM cadastre_issues GROUP BY 1 ORDER BY 2 DESC""").df()
    facts = con.execute("SELECT * FROM cadastre_file_facts").df()
    with pd.ExcelWriter(out / "00_summary.xlsx") as xw:
        status.to_excel(xw, sheet_name="status", index=False)
        cats.to_excel(xw, sheet_name="issue_categories", index=False)
        methods.to_excel(xw, sheet_name="mapped_by_method", index=False)
        cad_issues.to_excel(xw, sheet_name="cadastre_data_issues", index=False)
        facts.to_excel(xw, sheet_name="cadastre_file", index=False)

    # 3. review workbooks: one sheet per category, one line per distinct
    #    SPR street (row counts + example spr_ids) so each problem is read once
    def grouped(where):
        return con.execute(f"""
            SELECT issue_category, region, community, settlement, street, any_value(issue_detail) AS example_detail,
                   count(*) AS spr_rows, string_agg(spr_id::VARCHAR, ',' ORDER BY spr_id)[:200] AS spr_ids_first
            FROM spr_mapping_result WHERE {where}
            GROUP BY ALL ORDER BY spr_rows DESC""").df()

    def rows(where, limit=None):
        lim = f"LIMIT {limit}" if limit else ""
        return con.execute(f"""
            SELECT spr_id, region, community, settlement, street, house, house_type, apartment,
                   issue_category, issue_detail, suggested_cadastre_id, candidate_cadastre_ids
            FROM spr_mapping_result WHERE {where} ORDER BY region, community, settlement, street, spr_id {lim}""").df()

    def write_book(path, owner):
        cats_o = [c for c, (_, o, _) in ISSUES.items() if o == owner]
        with pd.ExcelWriter(path) as xw:
            pd.DataFrame([(c, ISSUES[c][0], ISSUES[c][2]) for c in cats_o],
                         columns=["issue_category", "match_status", "meaning"]).to_excel(xw, sheet_name="README", index=False)
            for cat in cats_o:
                n = con.execute("SELECT count(*) FROM spr_mapping_result WHERE issue_category = ?", [cat]).fetchone()[0]
                if n == 0:
                    continue
                sheet = cat[:31]
                if n <= 200_000 and cat not in ("SPR_FOREIGN_ADDRESS",):
                    rows(f"issue_category = '{cat}'").to_excel(xw, sheet_name=sheet, index=False)
                else:
                    grouped(f"issue_category = '{cat}'").to_excel(xw, sheet_name=sheet, index=False)
            if owner == "SPR":
                # not an error that blocks mapping: several SPR rows are one cadastre address
                con.execute("""
                    WITH d AS (SELECT cadastre_id FROM spr_mapping_result WHERE cadastre_id IS NOT NULL
                               GROUP BY 1 HAVING count(*) > 1)
                    SELECT r.cadastre_id, r.spr_id, r.region, r.community, r.settlement, r.street, r.house,
                           r.house_type, r.apartment
                    FROM spr_mapping_result r JOIN d USING (cadastre_id) ORDER BY r.cadastre_id, r.spr_id
                """).df().to_excel(xw, sheet_name="SPR_SAME_ADDRESS_MANY_ROWS", index=False)
            if owner == "CADASTRE":
                con.execute("""
                    SELECT i.issue_code, i.detail, c.* FROM cadastre_issues i JOIN cadastre c ON c.id = i.cadastre_id
                    ORDER BY i.issue_code, c.region, c.community, c.settlement, c.street_name, c.house
                """).df().to_excel(xw, sheet_name="cadastre_only_checks", index=False)

    write_book(out / "issues_for_cadastre.xlsx", "CADASTRE")
    write_book(out / "issues_for_spr.xlsx", "SPR")
    write_book(out / "needs_review.xlsx", "REVIEW")
    log(STEP, f"written to {out}")


def run():
    con = connect()
    ref = pd.DataFrame([(k, v[0], v[1], v[2]) for k, v in ISSUES.items()],
                       columns=["code", "match_status", "owner", "meaning"])
    con.register("_issue_ref", ref)
    build_result(con)
    con.execute("CREATE OR REPLACE TABLE issue_ref AS SELECT * FROM _issue_ref")
    con.unregister("_issue_ref")
    for st, conf, n in con.execute("""SELECT match_status, match_confidence, count(*) FROM spr_mapping_result
                                      GROUP BY ALL ORDER BY 3 DESC""").fetchall():
        log(STEP, f"   {st:14} {conf or '':8} {n:>9,}")
    export(con)
    con.close()


if __name__ == "__main__":
    run()
