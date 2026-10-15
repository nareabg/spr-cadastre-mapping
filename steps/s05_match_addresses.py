"""
Step 5 - From street to cadastre id: house, apartment, object type.

Rows whose street was found (step 4) get here, plus rows with ONE street
candidate that is only suggested or has a different type: for those the
same lookup gives a suggested cadastre id for the reviewer (never mapped).
Numbers are compared
EXACTLY (normalize.number_key): a different house or apartment number is a
different address, never a near match.

  candidates = cadastre rows on that street_uid with
               house_k == SPR house_k  and  building_k == SPR apartment_k
               (no SPR apartment -> cadastre building must be blank too)

  1 candidate            -> mapped (if its object type does not contradict
                            the SPR house type, e.g. a garage for an apartment)
  several candidates     -> the SPR house type (Տ, Շ, ՀՈՂ ...) picks the one
                            whose cadastre object type matches; if exactly one
                            matches -> mapped (address_pick = by_object_type);
                            if none matches exactly, one compatible object is
                            taken the same way

Object type check: same (exact type) | compatible (house / small house /
land plot: Տ, ՏՆԱԿ, ՀՈՂ <-> բնակելի տուն, հողամաս) | different | unknown.
A compatible object is mapped but never gets high confidence.
  otherwise              -> not mapped, with the reason

Output: spr_address_match (one row per spr_id that reached this step).
"""
import pandas as pd

from mapping.db import connect, save_df, log
from mapping.normalize import number_key
from mapping import nare_rules

STEP = "05_match_addresses"

# SPR house_type code -> cadastre house_type. A lookup of SPR's own
# abbreviations (like Փ. = փողոց); no address is renamed here.
HOUSE_TYPE_CODES = {
    "Տ": "բնակելի տուն", "ՏՈՒՆ": "բնակելի տուն",
    "Շ": "շենք", "ՇԵՆՔ": "շենք",
    "ՀՈՂ": "հողամաս",
    "ԱՅԳԵՏՆԱԿ": "այգետնակ", "ԱՎՏՈՏՆԱԿ": "ավտոտնակ", "ՄԱՍՆԱՇԵՆՔ": "մասնաշենք",
    "ԿԻՍԱԿԱՌՈՒՅՑ": "կիսակառույց", "ՇԻՆՈՒԹՅՈՒՆ": "շինություն",
}
APARTMENT_TYPES = {"բնակարան"}
# house / land plot compatibility and conflicts: nare_rules #2 and #3
HOUSE_PLOT_SPR_CODES = nare_rules.HOUSE_PLOT_SPR_CODES
HOUSE_PLOT_CAD_TYPES = nare_rules.HOUSE_PLOT_CAD_TYPES


def object_check(spr_code, has_apartment, cad_house_type, cad_building_type) -> str:
    """'same' | 'compatible' | 'different' | 'unknown' (one side does not say)."""
    if has_apartment:
        if cad_building_type is None or pd.isna(cad_building_type):
            return "unknown"
        if spr_code in ("", "Շ", "ՇԵՆՔ", "ԲՆԱԿԱՐԱՆ"):
            return "same" if cad_building_type in APARTMENT_TYPES else "different"
        return "unknown"
    expected = HOUSE_TYPE_CODES.get(spr_code)
    if cad_house_type is None or pd.isna(cad_house_type):
        return "unknown"
    if expected is not None and cad_house_type == expected:
        return "same"
    if nare_rules.HOUSE_PLOT_COMPATIBLE and spr_code in HOUSE_PLOT_SPR_CODES and cad_house_type in HOUSE_PLOT_CAD_TYPES:
        return "compatible"
    # a house-group code (ՏՆԱԿ too) facing a garage, shop ... is a different object
    if nare_rules.HOUSE_GROUP_OTHER_OBJECT_IS_CONFLICT and spr_code in HOUSE_PLOT_SPR_CODES:
        return "different"
    return "unknown" if expected is None else "different"


def run():
    con = connect()
    base = con.execute("""
        SELECT p.spr_id, coalesce(CAST(m.street_uid AS BIGINT), TRY_CAST(m.candidate_uids AS BIGINT)) AS street_uid,
               m.street_status = 'found' AS street_confirmed,
               p.house_k, p.apartment_k, p.house_type_k, m.tail_kind, m.tail
        FROM spr_prepared p JOIN spr_street_match m USING (spr_id)
        WHERE m.street_status = 'found'
           OR (m.street_status IN ('suggested', 'type_mismatch') AND m.candidate_uids NOT LIKE '%,%')
    """).df()
    log(STEP, f"rows with one street (found or suggested): {len(base):,}")

    # all cadastre rows of the same house on the same street (any building value)
    con.register("_base", base)
    house_rows = con.execute("""
        SELECT b.spr_id, k.cadastre_id, k.building_k, k.house_type, k.building_type
        FROM _base b JOIN cadastre_keys k
          ON k.street_uid = b.street_uid AND k.house_k = b.house_k
        WHERE b.house_k <> ''
    """).df()
    con.unregister("_base")

    hr = house_rows.merge(base[["spr_id", "apartment_k", "house_type_k"]], on="spr_id")
    hr["unit_match"] = hr["building_k"] == hr["apartment_k"]
    agg = hr.groupby("spr_id").agg(n_house_rows=("cadastre_id", "size"),
                                   n_house_with_unit=("building_k", lambda s: int((s != "").sum())))
    cand = hr[hr.unit_match].copy()
    cand["obj"] = [object_check(c, a != "", ht, bt) for c, a, ht, bt in
                   zip(cand.house_type_k, cand.apartment_k, cand.house_type, cand.building_type)]
    cand["sig"] = cand["house_type"].fillna("∅") + "|" + cand["building_type"].fillna("∅")

    # one candidate: mapped directly; several: decided per row
    n_cand = cand.groupby("spr_id")["cadastre_id"].transform("size")
    one = cand[n_cand == 1]
    results = {sid: ("mapped", cid, "single", obj, [cid])
               for sid, cid, obj in zip(one.spr_id, one.cadastre_id, one.obj)}
    for spr_id, g in cand[n_cand > 1].groupby("spr_id", sort=False):
        ids = g.cadastre_id.tolist()
        same = g[g.obj == "same"]
        compatible = g[g.obj == "compatible"]
        if len(same) == 1:
            results[spr_id] = ("mapped", same.cadastre_id.iloc[0], "by_object_type", "same", ids)
        elif len(same) == 0 and len(compatible) == 1:
            results[spr_id] = ("mapped", compatible.cadastre_id.iloc[0], "by_object_type", "compatible", ids)
        elif g.sig.nunique() == 1 or (len(same) > 1 and same.sig.nunique() == 1):
            results[spr_id] = ("cadastre_duplicate", None, None, None, ids)
        else:
            results[spr_id] = ("several_objects", None, None, None, ids)

    out = base[["spr_id", "street_uid", "street_confirmed", "house_k", "apartment_k", "house_type_k",
                "tail_kind", "tail"]].merge(
        agg, on="spr_id", how="left")
    out[["n_house_rows", "n_house_with_unit"]] = out[["n_house_rows", "n_house_with_unit"]].fillna(0).astype(int)
    r = pd.DataFrame.from_dict(results, orient="index",
                               columns=["address_status", "cadastre_id", "address_pick", "object_type_check",
                                        "address_candidates"])
    r.index.name = "spr_id"
    out = out.merge(r.reset_index(), on="spr_id", how="left")
    out["address_candidates"] = out["address_candidates"].map(lambda v: ",".join(v) if isinstance(v, list) else None)

    # a number left in the street text ('ԱՆԴՐԱՆԻԿԻ Փ. 15') must be the house number
    def strip_num(x):
        return number_key(x).replace("/", "").replace("-", "").replace(",", "")
    out["tail_conflict"] = (out["tail_kind"] == "digits") & (
        out["tail"].fillna("").map(strip_num) != out["house_k"].map(strip_num))

    def reason(r):
        if r["house_k"] == "":
            return "house_missing_in_spr"
        if r["tail_conflict"]:
            return "street_text_number_conflicts_house"
        if r["n_house_rows"] == 0:
            return "house_not_found"
        if r["apartment_k"] != "":
            return "apartment_not_found" if r["n_house_with_unit"] > 0 else "house_has_no_units_in_cadastre"
        return "apartment_missing_in_spr"
    miss = out["address_status"].isna()
    out.loc[miss, "address_status"] = out[miss].apply(reason, axis=1)
    # the only candidate is a different kind of object (a garage for an apartment,
    # a land plot for a house): never mapped, only suggested
    conflict_obj = (out["address_status"] == "mapped") & (out["object_type_check"] == "different")
    out.loc[conflict_obj, "address_status"] = "object_type_conflict"
    blocked = out["tail_conflict"] & (out["address_status"] != "street_text_number_conflicts_house")
    out.loc[blocked, "address_status"] = "street_text_number_conflicts_house"
    out.loc[blocked, ["cadastre_id", "address_pick", "object_type_check"]] = None
    # cadastre_id holds the address only when it is mapped; otherwise it is a suggestion
    out["suggested_cadastre_id"] = out["cadastre_id"].where(
        (out["address_status"] == "object_type_conflict") | ((out["address_status"] == "mapped") & ~out["street_confirmed"]))
    out.loc[out["address_status"] != "mapped", "cadastre_id"] = None
    out.loc[~out["street_confirmed"], "cadastre_id"] = None

    save_df(con, "spr_address_match", out.sort_values("spr_id"))
    for (conf, k), n in out.groupby(["street_confirmed", "address_status"]).size().items():
        log(STEP, f"   {'street found' if conf else 'street suggested':16} {k:36} {n:>9,}")
    con.close()


if __name__ == "__main__":
    run()
