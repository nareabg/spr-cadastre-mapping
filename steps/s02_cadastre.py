"""
Step 2 - Cadastre keys and cadastre data-quality issues.

  cadastre_keys     one row per cadastre id: cleaned geography, street_uid,
                    house / building keys. `cadastre` itself is never changed.
  cadastre_streets  one row per distinct cleaned street (street_uid) per settlement
  cadastre_issues   one row per (cadastre id, issue) for the cadastre team

Cadastre issue codes
  CAD_DUPLICATE_ADDRESS        several ids, same address and same object types
  CAD_SAME_ADDRESS_MULTI_OBJECT several ids, same address, different object types
                               (e.g. a house and its land plot) - not an error by
                               itself, but an SPR address can only take one id
  CAD_STREET_SPELLING_VARIANTS one street written in more than one way inside a
                               settlement ('Ք. Քըրքորյան' / 'Քըրք Քըրքորյան')
  CAD_STREET_NAME_MISSING      street type given, street name blank
  CAD_NONSTANDARD_HOUSE_NUMBER house holds words or lists ('2/1շենք,1-ին')
  CAD_LEADING_TRAILING_SPACES  a value starts or ends with a space
  (file level) cadastre_file_facts: rows per input part and whether a part
  stops exactly at Excel's row limit
"""
import pandas as pd

import config
from mapping.db import connect, save_df, log
from mapping.normalize import place_key, number_key, stem, collapse_doubles
from mapping.street_parser import cadastre_street, split_name

STEP = "02_cadastre"


def street_key(st) -> str:
    return " ".join(st.main) + "|" + (st.main_type or "") + "|" + " ".join(st.sub) + "|" + (st.sub_type or "")


def variant_key(st) -> str:
    """Same street written differently: word endings, doubled letters and
    initials ignored; word and number ORDER kept. Groups whose written
    initials disagree (Ա. Խաչատրյան / Մ. Խաչատրյան) are NOT variants; see
    mark_variants()."""
    words, _initials, codes = split_name(st.main)
    keep = set(words) | set(codes)
    words = [t if t in codes else stem(collapse_doubles(t)) for t in st.main if t in keep]
    return " ".join(words) + "|" + (st.main_type or "") + "|" + " ".join(st.sub) + "|" + (st.sub_type or "")


def initials_of(street_key: str) -> str:
    return " ".join(sorted(split_name(street_key.split("|")[0].split())[1]))


def mark_variants(streets: pd.DataFrame) -> pd.Series:
    """True for streets that share a variant_key with another street of the same
    settlement AND at most one distinct written initial set exists in the group."""
    g = ["region_k", "community_k", "settlement_k", "variant_key"]
    ini = streets["street_key"].map(initials_of)
    size = streets.groupby(g)["street_uid"].transform("size")
    n_ini = ini.where(ini != "").groupby([streets[c] for c in g]).transform("nunique").fillna(0)
    return (size > 1) & (n_ini <= 1)


def run():
    con = connect()
    cad = con.execute("SELECT * FROM cadastre").df()

    # ---- geography keys (computed on distinct values, then mapped back)
    for col in ("region", "community", "settlement"):
        u = {v: place_key(v) for v in cad[col].dropna().unique()}
        cad[col + "_k"] = cad[col].map(u).fillna("")

    # ---- street structure on distinct street tuples
    scols = ["street_name", "street_type", "sub_street_name", "sub_street_type"]
    st = cad[scols].drop_duplicates().reset_index(drop=True)
    parsed = [cadastre_street(*r) for r in st.itertuples(index=False, name=None)]
    st["street_key"] = [street_key(p) for p in parsed]
    st["variant_key"] = [variant_key(p) for p in parsed]
    cad = cad.merge(st, on=scols, how="left")

    cad["house_k"] = cad["house"].map(number_key)
    cad["building_k"] = cad["building"].map(number_key)

    # street_uid: one id per (region, community, settlement, cleaned street)
    g = ["region_k", "community_k", "settlement_k", "street_key"]
    cad["street_uid"] = cad.groupby(g, sort=True).ngroup() + 1

    keys = cad[["id", "region_k", "community_k", "settlement_k", "street_uid", "street_key", "variant_key",
                "house_k", "building_k", "house_type", "building_type"]].rename(columns={"id": "cadastre_id"})
    save_df(con, "cadastre_keys", keys)

    streets = (cad.groupby(["street_uid"] + g[:3] + ["street_key", "variant_key"], dropna=False)
               .agg(street_name=("street_name", "first"), street_type=("street_type", "first"),
                    sub_street_name=("sub_street_name", "first"), sub_street_type=("sub_street_type", "first"),
                    region=("region", "first"), community=("community", "first"), settlement=("settlement", "first"),
                    n_addresses=("id", "size"))
               .reset_index())
    streets["is_spelling_variant"] = mark_variants(streets)
    save_df(con, "cadastre_streets", streets)

    # ---- issues (set-based, in SQL)
    con.execute("""
        CREATE OR REPLACE TEMP VIEW ck AS
        SELECT k.*, c.street_name, c.street_type, c.house, c.building,
               coalesce(k.house_type, '∅') AS ht, coalesce(k.building_type, '∅') AS bt
        FROM cadastre_keys k JOIN cadastre c ON c.id = k.cadastre_id
    """)
    con.execute("""
        CREATE OR REPLACE TABLE cadastre_issues AS
        WITH full_grp AS (
            SELECT street_uid, house_k, building_k, ht, bt,
                   string_agg(cadastre_id, ',' ORDER BY cadastre_id) AS ids, count(*) AS n
            FROM ck GROUP BY ALL),
        addr_grp AS (
            SELECT street_uid, house_k, building_k,
                   string_agg(DISTINCT ht, ' / ' ORDER BY ht) AS types, count(*) AS n
            FROM ck GROUP BY ALL),
        variants AS (
            SELECT street_uid,
                   string_agg(coalesce(street_name, '') , ' / ') OVER w AS spellings,
                   count(*) OVER w AS n
            FROM cadastre_streets WHERE is_spelling_variant
            WINDOW w AS (PARTITION BY region_k, community_k, settlement_k, variant_key))
        SELECT ck.cadastre_id, 'CAD_DUPLICATE_ADDRESS' AS issue_code,
               'same address and same type; ids: ' || f.ids AS detail
        FROM ck JOIN full_grp f USING (street_uid, house_k, building_k, ht, bt) WHERE f.n > 1
        UNION ALL
        SELECT ck.cadastre_id, 'CAD_SAME_ADDRESS_MULTI_OBJECT',
               'object types at this address: ' || a.types
        FROM ck JOIN addr_grp a USING (street_uid, house_k, building_k)
               JOIN full_grp f USING (street_uid, house_k, building_k, ht, bt)
        WHERE a.n > 1 AND f.n = 1
        UNION ALL
        SELECT ck.cadastre_id, 'CAD_STREET_SPELLING_VARIANTS', 'same street spelled: ' || v.spellings
        FROM ck JOIN variants v USING (street_uid) WHERE v.n > 1
        UNION ALL
        SELECT cadastre_id, 'CAD_STREET_NAME_MISSING', 'street_type given, street_name blank'
        FROM ck WHERE street_name IS NULL AND street_type IS NOT NULL
        UNION ALL
        SELECT cadastre_id, 'CAD_NONSTANDARD_HOUSE_NUMBER', 'house = ' || house
        FROM ck WHERE regexp_matches(house, '[,]|[ա-ֆ]{2,}')
        UNION ALL
        SELECT c.id, 'CAD_LEADING_TRAILING_SPACES', 'a value starts or ends with a space'
        FROM cadastre c
        WHERE list_bool_or([v <> trim(v) FOR v IN [c.region, c.community, c.settlement, c.street_name,
              c.street_type, c.sub_street_name, c.sub_street_type, c.house, c.house_type, c.building,
              c.building_type] IF v IS NOT NULL])
    """)
    out = con.execute("SELECT issue_code, count(*) n FROM cadastre_issues GROUP BY 1 ORDER BY 2 DESC").df()

    con.execute("CREATE OR REPLACE TABLE cadastre_file_facts AS SELECT * FROM cadastre_parts")
    log(STEP, f"cadastre streets: {len(streets):,}")
    for code, n in zip(out["issue_code"], out["n"]):
        log(STEP, f"   {code:32} {n:>9,} ids")
    con.close()


if __name__ == "__main__":
    run()
