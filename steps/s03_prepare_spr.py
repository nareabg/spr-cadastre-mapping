"""
Step 3 - Put every SPR row on the cadastre's geography.

Output: spr_prepared (one row per spr_id). Original columns stay in `spr`.

Geography is resolved with the colleagues' precedence (01_hasce_address_staged):
  1 geo_alias        specific (community, settlement) corrections
  2 community_alias  community renames (Մաստարա -> Թալին ...)
  3 yerevan          SPR keeps the district in `community`; the cadastre keeps it
                     in `settlement` with community = Երևան
  4 gyumri           blank settlement -> Գյումրի
  5 as_is

Where to search (scopes, narrowest first):
  settlement given          -> that settlement (Yerevan: then the whole city)
  no settlement             -> the whole community
  no community              -> the whole region

geo_status
  ok                         geography exists in the cadastre
  region_missing             SPR region blank
  foreign_or_unknown_region  region is not one of the 11 cadastre regions (RUS, ԼՂՀ ...)
  community_not_in_cadastre  community does not exist in the cadastre for that region
  settlement_not_in_cadastre settlement does not exist under that community
  (Yerevan district typos are searched city-wide instead: geo_status = ok,
   geo_note = district_not_in_cadastre)
"""
import json
from collections import defaultdict

import pandas as pd

from mapping.aliases import GEO_ALIASES, COMMUNITY_ALIASES
from mapping.db import connect, save_df, log
from mapping.normalize import place_key, norm_place, number_key, is_blank

STEP = "03_prepare_spr"
YEREVAN = place_key("Երևան")
SHIRAK, GYUMRI = place_key("Շիրակ"), place_key("Գյումրի")


def run():
    con = connect()
    spr = con.execute("SELECT spr_id, region, community, settlement, street, house, house_type, apartment FROM spr").df()
    geo = con.execute("SELECT DISTINCT region_k, community_k, settlement_k FROM cadastre_keys").df()

    regions = set(geo.region_k)
    communities = set(zip(geo.region_k, geo.community_k))
    settlements = set(zip(geo.region_k, geo.community_k, geo.settlement_k))
    sett_to_comm = defaultdict(set)                       # (region, settlement) -> communities
    for r, c, s in settlements:
        sett_to_comm[(r, s)].add(c)

    geo_alias = {(norm_place(r), norm_place(c), norm_place(s)): (tc, ts) for r, c, s, tc, ts in GEO_ALIASES}
    comm_alias = {(norm_place(r), norm_place(c)): t for r, c, t in COMMUNITY_ALIASES}

    # resolve on distinct geography tuples (few thousand), then join back
    g = spr[["region", "community", "settlement"]].drop_duplicates()
    rows = []
    for region, community, settlement in g.itertuples(index=False, name=None):
        r = place_key(region)
        nr, nc, ns = norm_place(region), norm_place(community), norm_place(settlement)
        rule, comm_eff, sett_eff = "as_is", community, settlement
        if (nr, nc, ns) in geo_alias:
            tc, ts = geo_alias[(nr, nc, ns)]
            rule, comm_eff, sett_eff = "geo_alias", tc, (ts if ts is not None else settlement)
        elif (nr, nc) in comm_alias:
            rule, comm_eff = "community_alias", comm_alias[(nr, nc)]
        elif r == YEREVAN:
            rule, comm_eff, sett_eff = "yerevan", "Երևան", community
        elif r == SHIRAK and place_key(community) == GYUMRI and is_blank(settlement):
            rule, sett_eff = "gyumri", community
        c, s = place_key(comm_eff), place_key(sett_eff)

        note, hint = None, None
        if is_blank(region):
            status, scopes = "region_missing", []
        elif r not in regions:
            status, scopes = "foreign_or_unknown_region", []
        elif not c:
            status, scopes = "ok", [("region", (r,))]
            note = "community_missing"
        elif (r, c) not in communities:
            status, scopes = "community_not_in_cadastre", []
            if sett_to_comm.get((r, c)):
                hint = "is a settlement of community: " + ", ".join(sorted(sett_to_comm[(r, c)]))
        elif not s:
            status, scopes = "ok", [("community", (r, c))]
            note = "district_missing" if r == YEREVAN else "settlement_missing"
        elif (r, c, s) in settlements:
            status = "ok"
            scopes = [("settlement", (r, c, s))] + ([("community", (r, c))] if r == YEREVAN else [])
        elif r == YEREVAN:
            status, scopes, note = "ok", [("community", (r, c))], "district_not_in_cadastre"
        else:
            status, scopes = "settlement_not_in_cadastre", []
            other = sett_to_comm.get((r, s), set()) - {c}
            if other:
                hint = "settlement exists under community: " + ", ".join(sorted(other))
        rows.append({"region": region, "community": community, "settlement": settlement,
                     "geo_rule": rule, "region_k": r, "community_k": c, "settlement_k": s,
                     "geo_status": status, "geo_note": note, "geo_hint": hint,
                     "scopes": json.dumps(scopes, ensure_ascii=False)})
    geo_res = pd.DataFrame(rows)

    out = spr.merge(geo_res, on=["region", "community", "settlement"], how="left")
    out["house_k"] = out["house"].map(number_key)
    out["apartment_k"] = out["apartment"].map(number_key)
    out["house_type_k"] = out["house_type"].fillna("").str.strip().str.upper()
    # colleagues' data_completeness: % of the 6 address parts that are filled
    parts = ["region", "community", "settlement", "street", "house", "apartment"]
    out["data_completeness"] = (out[parts].apply(lambda c: ~c.map(is_blank)).sum(axis=1) * 100 / 6).round(2)
    out = out[["spr_id", "geo_rule", "region_k", "community_k", "settlement_k", "geo_status", "geo_note",
               "geo_hint", "scopes", "street", "house_k", "apartment_k", "house_type_k", "data_completeness"]]
    save_df(con, "spr_prepared", out.sort_values("spr_id"))

    log(STEP, f"rows: {len(out):,}")
    for k, n in out["geo_status"].value_counts().items():
        log(STEP, f"   geo_status {k:28} {n:>9,}")
    for k, n in out["geo_rule"].value_counts().items():
        log(STEP, f"   geo_rule   {k:28} {n:>9,}")
    con.close()


if __name__ == "__main__":
    run()
