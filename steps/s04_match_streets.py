"""
Step 4 - Find the cadastre street of every SPR row.

Runs mapping.street_matcher on each DISTINCT (search scopes, street text)
pair, then joins the result back to the rows.

Output: spr_street_match (one row per spr_id) with
  street_status, street_uid, candidate_uids, match_method, text_reading,
  searched_in, scope_widened, type_check, similarity_score, runner_up_score,
  tail_kind, tail, parsed_street

Rows without a street name in the SPR text ('', 'ՏՆԱԿ', 'ՏՈՒՆ6'):
  the cadastre also has street-less addresses (villages). If the settlement
  has ONLY street-less addresses in the cadastre, the row is matched to them
  (match_method = no_street); if the settlement also has named streets, the
  street-less candidate is only suggested.
"""
import json

import pandas as pd

from mapping.db import connect, save_df, log
from mapping.street_matcher import StreetMatcher
from mapping.street_parser import cadastre_street, is_house_only

STEP = "04_match_streets"


def run():
    con = connect()
    streets = con.execute("""SELECT street_uid, region_k, community_k, settlement_k,
                                    street_name, street_type, sub_street_name, sub_street_type
                             FROM cadastre_streets""").df()
    catalog = [(int(u), r, c, s, cadastre_street(n, t, sn, st)) for u, r, c, s, n, t, sn, st
               in streets.itertuples(index=False, name=None)]
    matcher = StreetMatcher(catalog)

    # street-less cadastre addresses per settlement
    streets["no_name"] = streets["street_name"].isna() & streets["street_type"].isna() & streets["sub_street_name"].isna()
    per_sett = streets.groupby(["region_k", "community_k", "settlement_k"])
    streetless = {k: (g.loc[g.no_name, "street_uid"].astype(int).tolist(), bool(g.no_name.all())) for k, g in per_sett}

    rows = con.execute("""SELECT spr_id, scopes, street, settlement_k, community_k
                          FROM spr_prepared WHERE geo_status = 'ok'""").df()
    todo = rows[["scopes", "street", "settlement_k", "community_k"]].drop_duplicates()
    log(STEP, f"distinct (scope, street) pairs to match: {len(todo):,}")

    results = []
    for i, (scopes_js, street, sett_k, comm_k) in enumerate(todo.itertuples(index=False, name=None)):
        scopes = [(lvl, tuple(key)) for lvl, key in json.loads(scopes_js)]
        if street is None or is_house_only(street):
            res = {"street_status": "no_street_in_spr", "street_uid": None, "candidate_uids": None,
                   "match_method": None, "text_reading": None, "searched_in": None, "scope_widened": None,
                   "type_check": None, "similarity_score": None, "runner_up_score": None,
                   "tail_kind": None, "tail": None, "parsed_street": None}
            lvl, key = scopes[0]
            if lvl == "settlement" and key in streetless and len(streetless[key][0]) == 1:
                uid, only = streetless[key][0][0], streetless[key][1]
                res.update(street_status="found" if only else "suggested", street_uid=uid if only else None,
                           candidate_uids=[uid], match_method="no_street", searched_in="settlement",
                           scope_widened=False, type_check="issue has no type")
        else:
            res = matcher.match(scopes, street, place_names=[p for p in (sett_k, comm_k) if p])
        res["candidate_uids"] = ",".join(map(str, res["candidate_uids"])) if res["candidate_uids"] else None
        results.append({"scopes": scopes_js, "street": street, "settlement_k": sett_k, "community_k": comm_k, **res})
        if (i + 1) % 20000 == 0:
            log(STEP, f"   {i + 1:,} done")
    res_df = pd.DataFrame(results)

    out = rows.merge(res_df, on=["scopes", "street", "settlement_k", "community_k"], how="left")
    out = out.drop(columns=["scopes", "street", "settlement_k", "community_k"])
    out["street_uid"] = out["street_uid"].astype("Int64")
    save_df(con, "spr_street_match", out.sort_values("spr_id"))

    log(STEP, f"rows matched at street level: {len(out):,}")
    for k, n in out["street_status"].value_counts().items():
        log(STEP, f"   {k:20} {n:>9,}")
    con.close()


if __name__ == "__main__":
    run()
