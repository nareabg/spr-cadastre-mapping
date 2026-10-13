"""
Helpers for looking inside the pipeline (used by walkthrough.ipynb).

Every function opens the working database read-only and closes it again, so
they can be called between steps without blocking the next step.

  q(sql)                      run any SQL on the working database -> DataFrame
  explain(spr_id)             everything the pipeline did with one SPR row
  search_cadastre(text, place=None)
                              cadastre streets whose name contains `text`
  streets_in(place)           every cadastre street of a settlement / district
  houses_on(street_uid)       every cadastre address on one cadastre street
  sample(issue_category, n)   random rows of one category
"""
import json

import duckdb
import pandas as pd

import config
from .normalize import fold, place_key
from .street_parser import parse_spr_street

pd.set_option("display.max_columns", 40)
pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)


def q(sql: str, params=None) -> pd.DataFrame:
    con = duckdb.connect(str(config.DB_FILE), read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def _tables():
    return set(q("SELECT table_name FROM information_schema.tables")["table_name"])


def explain(spr_id: int):
    """Print what each step decided for one SPR row."""
    from IPython.display import display
    t = _tables()
    row = q("SELECT * FROM spr WHERE spr_id = ?", [spr_id])
    print(f"== SPR row {spr_id}")
    display(row)
    if "spr_prepared" in t:
        p = q("SELECT * FROM spr_prepared WHERE spr_id = ?", [spr_id]).iloc[0]
        print(f"\n== Step 3 geography: rule={p.geo_rule}  status={p.geo_status}  note={p.geo_note}  hint={p.geo_hint}")
        print(f"   keys: region='{p.region_k}' community='{p.community_k}' settlement='{p.settlement_k}'")
        print(f"   search areas: {json.loads(p.scopes)}")
        print(f"   house_k='{p.house_k}' apartment_k='{p.apartment_k}' house_type='{p.house_type_k}'")
        print("\n== How the street text is read:")
        for name, st, kind, tail in parse_spr_street(p.street, [x for x in (p.settlement_k, p.community_k) if x]):
            print(f"   {name:22} {st.text():45} leftover: {kind} {tail}")
    if "spr_street_match" in t:
        m = q("SELECT * FROM spr_street_match WHERE spr_id = ?", [spr_id])
        if len(m):
            m = m.iloc[0]
            print(f"\n== Step 4 street: {m.street_status}  rule={m.match_method}  reading={m.text_reading}  "
                  f"searched_in={m.searched_in}  type_check={m.type_check}  score={m.similarity_score}")
            if m.candidate_uids:
                display(q(f"""SELECT street_uid, settlement, street_name, street_type, sub_street_name,
                                     sub_street_type, n_addresses FROM cadastre_streets
                              WHERE street_uid IN ({m.candidate_uids})"""))
    if "spr_address_match" in t:
        a = q("SELECT * FROM spr_address_match WHERE spr_id = ?", [spr_id])
        if len(a):
            a = a.iloc[0]
            print(f"\n== Step 5 address: {a.address_status}  pick={a.address_pick}  object_type={a.object_type_check}")
            print(f"   cadastre rows with this house on that street: {a.n_house_rows}")
            display(q("""SELECT k.cadastre_id, c.house, c.house_type, c.building, c.building_type
                         FROM cadastre_keys k JOIN cadastre c ON c.id = k.cadastre_id
                         WHERE k.street_uid = ? AND k.house_k = ?""", [int(a.street_uid), a.house_k]))
    if "spr_mapping_result" in t:
        r = q("""SELECT cadastre_id, match_status, match_confidence, issue_category, issue_owner, issue_detail,
                        suggested_cadastre_id FROM spr_mapping_result WHERE spr_id = ?""", [spr_id])
        print("\n== Final")
        display(r)


def search_cadastre(text: str, place: str = None, limit: int = 50) -> pd.DataFrame:
    """Cadastre streets whose name contains `text` (any case, spelling folded)."""
    where, params = ["street_key LIKE '%' || ? || '%'"], [fold(text)]
    if place:
        where.append("(settlement_k = ? OR community_k = ?)")
        params += [place_key(place), place_key(place)]
    return q(f"""SELECT street_uid, region, community, settlement, street_name, street_type,
                        sub_street_name, sub_street_type, n_addresses
                 FROM cadastre_streets WHERE {' AND '.join(where)}
                 ORDER BY region, community, settlement, street_name LIMIT {limit}""", params)


def streets_in(place: str) -> pd.DataFrame:
    """All cadastre streets of a settlement (or Yerevan district)."""
    return q("""SELECT street_uid, settlement, street_name, street_type, sub_street_name, sub_street_type, n_addresses
                FROM cadastre_streets WHERE settlement_k = ? ORDER BY street_name, sub_street_name""",
             [place_key(place)])


def houses_on(street_uid: int) -> pd.DataFrame:
    """All cadastre addresses on one cadastre street."""
    return q("""SELECT c.id AS cadastre_id, c.house, c.house_type, c.building, c.building_type
                FROM cadastre_keys k JOIN cadastre c ON c.id = k.cadastre_id
                WHERE k.street_uid = ? ORDER BY k.house_k, k.building_k""", [street_uid])


def sample(issue_category: str = None, n: int = 20, status: str = None) -> pd.DataFrame:
    """Random rows of one issue category (or one match_status)."""
    cond = "issue_category = ?" if issue_category else "match_status = ?"
    return q(f"""SELECT spr_id, region, community, settlement, street, house, house_type, apartment,
                        cadastre_id, match_status, match_confidence, issue_category, issue_detail,
                        street_match_method
                 FROM spr_mapping_result WHERE {cond} ORDER BY random() LIMIT {n}""",
             [issue_category or status])
