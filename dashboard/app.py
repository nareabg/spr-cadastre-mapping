"""
SPR → Cadastre mapping dashboard.

Start it from the project folder:
    streamlit run dashboard/app.py
(or double-click run_dashboard.bat on Windows). It opens in the browser at
http://localhost:8501 and runs only on this computer.

Reads the pipeline's outputs (see dashboard/prepare_data.py); changes nothing.
"""
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_data  # noqa: E402
from steps.s06_export import ISSUES  # noqa: E402  (issue codes and their meaning)

st.set_page_config(page_title="SPR → Cadastre mapping", page_icon="🗺️", layout="wide",
                   initial_sidebar_state="collapsed")
# use the full screen width: smaller side margins than Streamlit's default
st.markdown("""<style>
[data-testid="stMainBlockContainer"], .block-container {padding-left: 1.5rem; padding-right: 1.5rem; max-width: 100%;}
</style>""", unsafe_allow_html=True)

STATUS_ORDER = ["mapped", "suggested", "ambiguous", "not_found", "out_of_scope"]
STATUS_COLORS = {"mapped": "#2f7d4f", "suggested": "#2d64a8", "ambiguous": "#6d4aa8",
                 "not_found": "#b0463c", "out_of_scope": "#8a918d"}
OWNER_COLORS = {"CADASTRE": "#a76712", "SPR": "#b0463c", "REVIEW": "#2d64a8"}
STATUS_HELP = {
    "mapped": "one cadastre id, every part of the address agrees",
    "suggested": "a likely cadastre id that needs a reviewer's yes",
    "ambiguous": "several cadastre ids fit equally well",
    "not_found": "no cadastre address fits",
    "out_of_scope": "foreign or blank region",
}
CAD_CHECKS = {
    "CAD_DUPLICATE_ADDRESS": "several ids, same address and same object type",
    "CAD_SAME_ADDRESS_MULTI_OBJECT": "several ids at one address with different object types (house + land plot ...)",
    "CAD_STREET_NAME_MISSING": "street type given, street name blank",
    "CAD_NONSTANDARD_HOUSE_NUMBER": "house number holds words or lists (2/1շենք,1-ին)",
    "CAD_LEADING_TRAILING_SPACES": "a value starts or ends with a space",
    "CAD_STREET_SPELLING_VARIANTS": "one street written two ways in a settlement",
}


# ------------------------------------------------------------------ data
@st.cache_resource(show_spinner="Loading data ...")
def db():
    if prepare_data.needs_rebuild():
        with st.spinner("Preparing dashboard data from the latest outputs (about 1 minute) ..."):
            prepare_data.build(log=lambda m: None)
    con = duckdb.connect()
    for name in ("spr_result", "cadastre", "cadastre_issues"):
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)", [str(prepare_data.DATA / f"{name}.parquet")])
    return con


def q(sql, params=None) -> pd.DataFrame:
    return db().execute(sql, params or []).df()


def one(sql, params=None):
    return db().execute(sql, params or []).fetchone()[0]


# a new pipeline run makes the loaded data stale: drop it, so a page refresh is enough
if prepare_data.needs_rebuild():
    db.clear()


def distinct(table, col, where="TRUE", params=None):
    return q(f"SELECT DISTINCT {col} AS v FROM {table} WHERE {col} IS NOT NULL AND {where} ORDER BY 1",
             params)["v"].tolist()


# ------------------------------------------------------------------ widgets
def split_ids(text):
    """'1001, 1002 1003;1004' -> ['1001', '1002', '1003', '1004'] (order kept, repeats dropped)."""
    return list(dict.fromkeys(t for t in re.split(r"[\s,;]+", text or "") if t))


class Filter:
    """Collects WHERE conditions and their parameters."""

    def __init__(self):
        self.conds, self.params = [], []

    def add(self, cond, *params):
        self.conds.append(cond)
        self.params.extend(params)

    def eq_any(self, col, values):
        if values:
            self.add(f"{col} IN ({', '.join('?' * len(values))})", *values)

    def ids_in(self, cols, text, list_cols=()):
        """Rows whose id is one of the typed ids. `text` may hold several ids
        separated by commas, spaces, semicolons or new lines. `cols` hold one id
        each; `list_cols` hold comma-separated id lists (candidate ids)."""
        ids = split_ids(text)
        if not ids:
            return
        parts = [f"{c} IN ({', '.join('?' * len(ids))})" for c in cols]
        parts += [f"list_has_any(string_split(coalesce({c}, ''), ','), ?::VARCHAR[])" for c in list_cols]
        params = [i for _ in cols for i in ids] + [ids for _ in list_cols]
        self.add("(" + " OR ".join(parts) + ")", *params)

    def contains(self, col, text):
        if text and text.strip():
            self.add(f"lower(coalesce({col}, '')) LIKE '%' || lower(?) || '%'", text.strip())

    @property
    def sql(self):
        return " AND ".join(self.conds) if self.conds else "TRUE"


def geo_filters(key, table, f: Filter, cols=("region", "community", "settlement"), labels=None):
    """Region -> community -> settlement, each list narrowed by the one before."""
    labels = labels or ("Region", "Community / district", "Settlement")
    c1, c2, c3 = st.columns(3)
    region = c1.selectbox(labels[0], ["(all)"] + distinct(table, cols[0]), key=f"{key}_r")
    sub = Filter()
    if region != "(all)":
        f.add(f"{cols[0]} = ?", region)
        sub.add(f"{cols[0]} = ?", region)
    community = c2.selectbox(labels[1], ["(all)"] + distinct(table, cols[1], sub.sql, sub.params), key=f"{key}_c")
    if community != "(all)":
        f.add(f"{cols[1]} = ?", community)
        sub.add(f"{cols[1]} = ?", community)
    settlement = c3.selectbox(labels[2], ["(all)"] + distinct(table, cols[2], sub.sql, sub.params), key=f"{key}_s")
    if settlement != "(all)":
        f.add(f"{cols[2]} = ?", settlement)
    return region, community, settlement


ID_COLUMNS = ["spr_id", "cadastre_id"]      # the first one present stays visible while scrolling sideways


def table_with_download(key, table, f: Filter, columns="*", order="1", column_sets=None):
    """Paged table + CSV download. column_sets = {name: [columns]} adds a picker
    so a wide table can be shown a few columns at a time; the CSV always has
    every column."""
    shown = columns
    if column_sets:
        pick = st.radio("Columns to show", list(column_sets), horizontal=True, key=f"{key}_colset")
        shown = ", ".join(column_sets[pick])
    n = one(f"SELECT count(*) FROM {table} WHERE {f.sql}", f.params)
    c1, c2, c3 = st.columns([3, 1, 1])
    size = c2.selectbox("Rows per page", [100, 1_000, 5_000, 20_000], index=1, key=f"{key}_size")
    pages = max(1, -(-n // size))
    if st.session_state.get(f"{key}_page", 1) > pages:     # filters changed: fewer pages now
        st.session_state[f"{key}_page"] = 1
    page =c3.number_input(f"Page (of {pages:,})", min_value=1, max_value=pages, step=1, key=f"{key}_page")
    first = (page - 1) * size
    c1.markdown(f"**{n:,} rows** match the filters  \nshowing rows {min(first + 1, n):,}–{min(first + size, n):,}"
                " · use the page box to move on, or download them all below")
    df = q(f"SELECT {shown} FROM {table} WHERE {f.sql} ORDER BY {order} LIMIT {size} OFFSET {first}", f.params)
    pin = next((c for c in ID_COLUMNS if c in df.columns), None)      # spr_id, else cadastre_id
    pins = {pin: st.column_config.Column(pinned=True)} if pin else None
    st.dataframe(df, width="stretch", hide_index=True, column_config=pins,
                 height=min(720, 38 + 35 * max(len(df), 1)))
    if n:
        if st.button(f"Prepare a CSV of all {n:,} rows", key=f"{key}_prep"):
            full = q(f"SELECT {columns} FROM {table} WHERE {f.sql} ORDER BY {order}", f.params)
            st.session_state[f"{key}_csv"] = full.to_csv(index=False).encode("utf-8-sig")
        if f"{key}_csv" in st.session_state:
            st.download_button("Download CSV", st.session_state[f"{key}_csv"], file_name=f"{key}.csv",
                               mime="text/csv", key=f"{key}_dl")
    return n


def kpis(items):
    cols = st.columns(len(items))
    for c, (label, value, help_) in zip(cols, items):
        c.metric(label, f"{value:,}" if isinstance(value, (int, float)) else value, help=help_)


def bar(df, x, y, color=None, colors=None, horizontal=False, height=380, title=None):
    if df.empty:
        st.info("Nothing to show for these filters.")
        return
    fig = px.bar(df, x=y if horizontal else x, y=x if horizontal else y, color=color,
                 color_discrete_map=colors, orientation="h" if horizontal else "v", title=title, text_auto=",")
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=40 if title else 10, b=10), legend_title_text="")
    if horizontal:
        fig.update_yaxes(categoryorder="total ascending", title="")
        fig.update_xaxes(title="")
    else:
        fig.update_xaxes(title="")
        fig.update_yaxes(title="")
    st.plotly_chart(fig, width="stretch")


# ------------------------------------------------------------------ page
st.title("SPR → Cadastre address mapping")
st.caption("Explore the cadastre, the mapping result and every issue found. Filters narrow each tab; "
           "every table can be downloaded as CSV.")

with st.sidebar:
    st.header("About the data")
    st.write(f"Built from `output/` and `data/input/` on "
             f"{pd.Timestamp.fromtimestamp((prepare_data.DATA / 'spr_result.parquet').stat().st_mtime):%d %b %Y %H:%M}"
             if (prepare_data.DATA / "spr_result.parquet").exists() else "Data not built yet.")
    if st.button("Reload after a new pipeline run"):
        prepare_data.build(log=lambda m: None)
        st.cache_resource.clear()
        st.rerun()
    st.divider()
    st.markdown("**Statuses**")
    for s in STATUS_ORDER:
        st.markdown(f"- **{s}**: {STATUS_HELP[s]}")
    st.markdown("**Owners**\n- **CADASTRE**: cadastre data blocks the match\n- **SPR**: the SPR row is incomplete "
                "or wrong\n- **REVIEW**: someone has to decide")

tabs = st.tabs(["📊 Overview", "🏠 Cadastre data", "⚠️ SPR issues", "✅ SPR mapping result",
                "🧾 Cadastre issues", "📖 Glossary"])

# ================================================================== overview
with tabs[0]:
    total = one("SELECT count(*) FROM spr_result")
    by_status = q("SELECT match_status, coalesce(match_confidence, '') AS confidence, count(*) AS rows "
                  "FROM spr_result GROUP BY ALL")
    get = lambda s, c="": int(by_status.loc[(by_status.match_status == s) & (by_status.confidence == c), "rows"].sum())
    kpis([("SPR rows", total, None),
          ("Mapped · high", get("mapped", "high"), "strongest rules, single id, object type agrees"),
          ("Mapped · medium", get("mapped", "medium"), "all other mapped rows"),
          ("Suggested", get("suggested"), STATUS_HELP["suggested"]),
          ("Ambiguous", get("ambiguous"), STATUS_HELP["ambiguous"]),
          ("Not found", get("not_found"), STATUS_HELP["not_found"]),
          ("Out of scope", get("out_of_scope"), STATUS_HELP["out_of_scope"])])

    c1, c2 = st.columns([1, 1.4])
    with c1:
        s = q("SELECT match_status, count(*) AS rows FROM spr_result GROUP BY 1")
        fig = px.pie(s, names="match_status", values="rows", hole=0.55, color="match_status",
                     color_discrete_map=STATUS_COLORS, category_orders={"match_status": STATUS_ORDER},
                     title="All SPR rows by status")
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(height=400, showlegend=False, margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig, width="stretch")
    with c2:
        cat = q("SELECT issue_category, issue_owner, count(*) AS rows FROM spr_result "
                "WHERE issue_category IS NOT NULL GROUP BY ALL")
        bar(cat, "issue_category", "rows", color="issue_owner", colors=OWNER_COLORS, horizontal=True, height=400,
            title="Why rows are not mapped (by who should act)")

    st.subheader("By region")
    reg = q("""SELECT region, match_status, count(*) AS rows FROM spr_result
               WHERE match_status <> 'out_of_scope' GROUP BY ALL""")
    fig = px.bar(reg, x="region", y="rows", color="match_status", color_discrete_map=STATUS_COLORS,
                 category_orders={"match_status": STATUS_ORDER})
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend_title_text="", xaxis_title="",
                      yaxis_title="SPR rows")
    st.plotly_chart(fig, width="stretch")
    pivot = reg.pivot_table(index="region", columns="match_status", values="rows", fill_value=0)
    pivot = pivot[[c for c in STATUS_ORDER if c in pivot.columns]]
    pivot["mapped %"] = (100 * pivot.get("mapped", 0) / pivot.sum(axis=1)).round(1)
    st.dataframe(pivot.sort_values("mapped %"), width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Mapped rows: which rule found the street")
        m = q("""SELECT street_match_method AS rule, match_confidence AS confidence, count(*) AS rows
                 FROM spr_result WHERE match_status = 'mapped' GROUP BY ALL""")
        bar(m, "rule", "rows", color="confidence", colors={"high": "#2f7d4f", "medium": "#8fc1a0"}, height=360)
    with c2:
        st.subheader("Earlier mapping vs this mapping")
        e = q("""SELECT coalesce(earlier_match_type, 'not matched earlier') AS earlier, match_status AS now,
                        count(*) AS rows FROM spr_result WHERE match_status <> 'out_of_scope' GROUP BY ALL""")
        bar(e, "earlier", "rows", color="now", colors=STATUS_COLORS, height=360)
        st.caption("`AMBIGUOUS` = rows the earlier mapping left with several candidate ids.")

# ================================================================== cadastre data
with tabs[1]:
    st.subheader("Cadastre addresses")
    f = Filter()
    geo_filters("cad", "cadastre", f)
    c1, c2, c3, c4 = st.columns([2, 1, 2, 2])
    f.contains("street_name", c1.text_input("Street name contains", key="cad_street"))
    house = c2.text_input("House", key="cad_house")
    if house.strip():
        f.add("trim(house) = ?", house.strip())
    f.eq_any("house_type", c3.multiselect("House type", distinct("cadastre", "house_type"), key="cad_ht"))
    used = c4.selectbox("Used by SPR rows?", ["(any)", "yes, mapped by SPR", "no"], key="cad_used")
    if used.startswith("yes"):
        f.add("spr_rows_mapped > 0")
    elif used == "no":
        f.add("spr_rows_mapped = 0")
    c1, c2 = st.columns([1, 3])
    if c1.checkbox("Only ids with a data issue", key="cad_iss"):
        f.add("cadastre_issues IS NOT NULL")
    f.ids_in(["cadastre_id"], c2.text_input("Cadastre id(s), separated by commas", key="cad_id"))

    kpis([("Cadastre ids", one(f"SELECT count(*) FROM cadastre WHERE {f.sql}", f.params), None),
          ("Streets", one(f"SELECT count(DISTINCT (settlement, street_name, street_type, sub_street_name)) "
                          f"FROM cadastre WHERE {f.sql}", f.params), None),
          ("Settlements", one(f"SELECT count(DISTINCT (community, settlement)) FROM cadastre WHERE {f.sql}",
                              f.params), None),
          ("Ids with a data issue", one(f"SELECT count(*) FROM cadastre WHERE {f.sql} AND cadastre_issues IS NOT NULL",
                                        f.params), None),
          ("Ids mapped by SPR", one(f"SELECT count(*) FROM cadastre WHERE {f.sql} AND spr_rows_mapped > 0",
                                    f.params), None)])
    c1, c2 = st.columns(2)
    level = "settlement" if any("community" in c for c in f.conds) else ("community" if any(
        "region" in c for c in f.conds) else "region")
    with c1:
        d = q(f"SELECT {level}, count(*) AS ids FROM cadastre WHERE {f.sql} GROUP BY 1 ORDER BY 2 DESC LIMIT 25",
              f.params)
        bar(d, level, "ids", horizontal=True, title=f"Cadastre ids by {level} (top 25)")
    with c2:
        d = q(f"SELECT coalesce(house_type, '(blank)') AS house_type, count(*) AS ids FROM cadastre "
              f"WHERE {f.sql} GROUP BY 1 ORDER BY 2 DESC LIMIT 15", f.params)
        bar(d, "house_type", "ids", horizontal=True, title="Object type (top 15)")
    table_with_download("cadastre", "cadastre", f,
                        order="region, community, settlement, street_name, sub_street_name, house, building")

# ================================================================== SPR issues
with tabs[2]:
    st.subheader("SPR rows that are not mapped, and why")
    f = Filter()
    f.add("issue_category IS NOT NULL")
    c1, c2, c3 = st.columns([1, 2, 1])
    owners = c1.multiselect("Owner", ["CADASTRE", "SPR", "REVIEW"], key="iss_owner")
    f.eq_any("issue_owner", owners)
    cats = [c for c, v in ISSUES.items() if not owners or v[1] in owners]
    chosen = c2.multiselect("Issue category", cats, key="iss_cat",
                            format_func=lambda c: f"{c} — {ISSUES[c][2]}")
    f.eq_any("issue_category", chosen)
    f.eq_any("match_status", c3.multiselect("Status", ["suggested", "ambiguous", "not_found", "out_of_scope"],
                                            key="iss_status"))
    geo_filters("iss", "spr_result", f)
    c1, c2, c3, c4 = st.columns(4)
    f.contains("street", c1.text_input("SPR street contains", key="iss_street"))
    f.ids_in(["CAST(spr_id AS VARCHAR)"], c2.text_input("spr_id(s), separated by commas", key="iss_sprid"))
    f.ids_in(["suggested_cadastre_id"], c3.text_input("Cadastre id(s): suggested or candidate", key="iss_cid"),
             list_cols=["candidate_cadastre_ids"])
    f.ids_in([], c4.text_input("Earlier mapping's candidate id(s)", key="iss_eid"),
             list_cols=["earlier_candidate_ids"])

    kpis([("Rows with an issue", one(f"SELECT count(*) FROM spr_result WHERE {f.sql}", f.params), None),
          ("Owner CADASTRE", one(f"SELECT count(*) FROM spr_result WHERE {f.sql} AND issue_owner='CADASTRE'",
                                 f.params), None),
          ("Owner SPR", one(f"SELECT count(*) FROM spr_result WHERE {f.sql} AND issue_owner='SPR'", f.params), None),
          ("Owner REVIEW", one(f"SELECT count(*) FROM spr_result WHERE {f.sql} AND issue_owner='REVIEW'",
                               f.params), None)])
    d = q(f"SELECT issue_category, issue_owner, count(*) AS rows FROM spr_result WHERE {f.sql} GROUP BY ALL",
          f.params)
    bar(d, "issue_category", "rows", color="issue_owner", colors=OWNER_COLORS, horizontal=True, height=420)

    st.markdown("#### Biggest problems first: one line per SPR street")
    g = q(f"""SELECT issue_category, region, community, settlement, street, count(*) AS spr_rows,
                     any_value(issue_detail) AS example_detail, min(spr_id) AS example_spr_id
              FROM spr_result WHERE {f.sql} GROUP BY ALL ORDER BY spr_rows DESC LIMIT 500""", f.params)
    st.dataframe(g, width="stretch", hide_index=True, height=360)

    st.markdown("#### Every row")
    table_with_download("spr_issues", "spr_result", f,
                        columns="spr_id, region, community, settlement, street, house, house_type, apartment, "
                                "match_status, issue_category, issue_owner, issue_detail, suggested_cadastre_id, "
                                "candidate_cadastre_ids, earlier_match_type, earlier_candidate_ids, street_match_method, "
                                "data_completeness",
                        order="region, community, settlement, street, spr_id")

# ================================================================== SPR mapping result
with tabs[3]:
    st.subheader("SPR mapping result: every row, every column")
    f = Filter()
    c1, c2, c3, c4 = st.columns(4)
    f.eq_any("match_status", c1.multiselect("Status", STATUS_ORDER, key="res_status"))
    f.eq_any("match_confidence", c2.multiselect("Confidence", ["high", "medium"], key="res_conf"))
    f.eq_any("street_match_method", c3.multiselect("Street rule", distinct("spr_result", "street_match_method"),
                                                   key="res_rule"))
    f.eq_any("earlier_match_type", c4.multiselect("Earlier mapping", ["AMBIGUOUS"], key="res_earlier"))
    geo_filters("res", "spr_result", f)
    c1, c2, c3, c4 = st.columns(4)
    f.contains("street", c1.text_input("SPR street contains", key="res_street"))
    f.contains("cad_street_name", c2.text_input("Cadastre street contains", key="res_cstreet"))
    f.ids_in(["CAST(spr_id AS VARCHAR)"], c3.text_input("spr_id(s), separated by commas", key="res_sprid"))
    f.ids_in(["cadastre_id", "suggested_cadastre_id"],
             c4.text_input("Cadastre id(s): mapped, suggested or candidate", key="res_cid"),
             list_cols=["candidate_cadastre_ids"])
    c1, _ = st.columns([1, 3])
    f.ids_in([], c1.text_input("Earlier mapping's candidate id(s)", key="res_eid"),
             list_cols=["earlier_candidate_ids"])
    smin, smax = st.slider("Similarity score (narrows only the rows matched by similarity; to see just those, pick Street rule = fuzzy)", 80, 100, (80, 100),
                           key="res_score")
    if (smin, smax) != (80, 100):
        # rows without a score (not matched by similarity) stay in; the slider only narrows scored rows
        f.add("(similarity_score IS NULL OR similarity_score BETWEEN ? AND ?)", smin, smax)

    # all columns; the earlier mapping's ids sit next to this mapping's candidates for easy comparison
    cols = q("SELECT * FROM spr_result LIMIT 0").columns.tolist()
    early = ["earlier_match_type", "earlier_candidate_ids"]
    cols = [c for c in cols if c not in early]
    cols[cols.index("candidate_cadastre_ids") + 1:1] = early
    spr_cols = ["spr_id", "region", "community", "settlement", "street", "house", "house_type", "apartment"]
    sets = {
        "All columns": cols,
        "Result": spr_cols + ["cadastre_id", "match_status", "match_confidence", "issue_category", "issue_owner",
                              "suggested_cadastre_id", "candidate_cadastre_ids"],
        "SPR next to cadastre": spr_cols + ["cadastre_id", "suggested_cadastre_id"]
                                + [c for c in cols if c.startswith("cad_")],
        "How it was matched": ["spr_id", "street", "cadastre_id", "match_status", "match_confidence",
                               "street_match_method", "text_reading", "searched_in", "type_check",
                               "similarity_score", "address_pick", "object_type_check", "geo_rule",
                               "data_completeness"],
        "Issues": ["spr_id", "street", "house", "apartment", "match_status", "issue_category", "issue_owner",
                   "issue_detail", "suggested_cadastre_id", "candidate_cadastre_ids"],
        "Earlier mapping": ["spr_id", "street", "house", "apartment", "cadastre_id", "match_status",
                            "candidate_cadastre_ids", "earlier_match_type", "earlier_candidate_ids"],
    }
    table_with_download("spr_mapping_result", "spr_result", f, columns=", ".join(cols), order="spr_id",
                        column_sets={k: [c for c in v if c in cols] for k, v in sets.items()})

    st.markdown("#### Look at one row")
    pick = st.text_input("Type an spr_id to see the SPR address next to its cadastre address", key="res_pick")
    if pick.strip().isdigit():
        r = q("SELECT * FROM spr_result WHERE spr_id = ?", [int(pick.strip())])
        if r.empty:
            st.warning("No row with that spr_id.")
        else:
            r = r.iloc[0]
            st.markdown(f"**Status:** `{r.match_status}` {('· ' + r.match_confidence) if r.match_confidence else ''}"
                        f"{(' · ' + r.issue_category + ' (' + str(r.issue_owner) + ')') if r.issue_category else ''}")
            if r.issue_detail:
                st.info(r.issue_detail)
            a, b = st.columns(2)
            spr_side = {k: r[k] for k in ["region", "community", "settlement", "street", "house", "house_type",
                                          "apartment"]}
            cad_side = {k.replace("cad_", ""): r[k] for k in r.index if k.startswith("cad_")}
            a.markdown("**SPR (as written)**")
            a.table(pd.Series(spr_side, name="value").fillna(""))
            b.markdown(f"**Cadastre id {r.cadastre_id or r.suggested_cadastre_id or '—'}**"
                       + (" (suggested)" if not r.cadastre_id and r.suggested_cadastre_id else ""))
            b.table(pd.Series(cad_side, name="value").fillna(""))
            how = {k: r[k] for k in ["street_match_method", "text_reading", "searched_in", "type_check",
                                     "similarity_score", "address_pick", "object_type_check", "geo_rule",
                                     "candidate_cadastre_ids", "earlier_match_type", "earlier_candidate_ids"]}
            st.markdown("**How it was decided**")
            st.table(pd.Series(how, name="value").astype(str).replace({"None": "", "nan": ""}))

# ================================================================== cadastre issues
with tabs[4]:
    st.subheader("Problems inside the cadastre data itself")
    st.caption("Found by checking the cadastre on its own, without SPR. One line per (cadastre id, problem).")
    f = Filter()
    c1, c2 = st.columns([2, 1])
    f.eq_any("issue_code", c1.multiselect("Problem", list(CAD_CHECKS), key="ci_code",
                                         format_func=lambda c: f"{c} — {CAD_CHECKS[c]}"))
    cids = c2.text_input("Cadastre id(s), separated by commas", key="ci_id")
    if split_ids(cids):
        # the id itself, or an id listed in a duplicate group's "ids: ..." detail
        f.add("(cadastre_id IN (SELECT unnest(?::VARCHAR[])) OR (issue_code = 'CAD_DUPLICATE_ADDRESS' AND "
              "list_has_any(string_split(regexp_replace(detail, '^.*ids: ', ''), ','), ?::VARCHAR[])))",
              split_ids(cids), split_ids(cids))
    geo_filters("ci", "cadastre_issues", f)
    f.contains("street_name", st.text_input("Street name contains", key="ci_street"))

    d = q(f"SELECT issue_code, count(*) AS ids FROM cadastre_issues WHERE {f.sql} GROUP BY 1", f.params)
    kpis([(code.replace("CAD_", "").replace("_", " ").lower(), int(d.loc[d.issue_code == code, "ids"].sum()),
           CAD_CHECKS[code]) for code in CAD_CHECKS])
    c1, c2 = st.columns(2)
    with c1:
        bar(d, "issue_code", "ids", horizontal=True, title="Cadastre ids per problem")
    with c2:
        r = q(f"SELECT region, issue_code, count(*) AS ids FROM cadastre_issues WHERE {f.sql} GROUP BY ALL",
              f.params)
        bar(r, "region", "ids", color="issue_code", title="By region")
    table_with_download("cadastre_issues", "cadastre_issues", f,
                        columns="issue_code, detail, cadastre_id, region, community, settlement, street_name, "
                                "street_type, sub_street_name, sub_street_type, house, house_type, building, "
                                "building_type",
                        order="issue_code, region, community, settlement, street_name, house, building")

# ================================================================== glossary
with tabs[5]:
    st.subheader("What every code means")
    st.markdown("#### SPR issue categories")
    st.dataframe(pd.DataFrame([(c, v[0], v[1], v[2]) for c, v in ISSUES.items()],
                              columns=["issue_category", "status", "owner", "meaning"]),
                 width="stretch", hide_index=True)
    st.markdown("#### Cadastre problems")
    st.dataframe(pd.DataFrame(list(CAD_CHECKS.items()), columns=["issue_code", "meaning"]),
                 width="stretch", hide_index=True)
    st.markdown("""
#### How a street is found (first rule that works decides)
| rule | allows | confidence |
|---|---|---|
| exact | identical cleaned name | high |
| alias | colleagues' renames (Գյումրի, Կապան) | high |
| double_letter | typo list, doubled letters | high |
| word_form | genitive endings (`ԳԱՌՆԻԻ` = `Գառնու`) | high |
| extra_words | SPR words inside a longer cadastre name (`ԱՆԴՐԱՆԻԿԻ` = `Զորավար Անդրանիկի`) | medium |
| initial_for_name | `Ք.` = `ՔԸՐՔ`, or one side omits the initial | medium |
| fuzzy | similar spelling, score ≥ 90 and clear of the next street | medium |

House and apartment numbers are never fuzzy: they must be identical.

#### Other columns
- **text_reading**: how the SPR street text was read (`as_written`, `neighbourhood_street`, `lane_of_street` …)
- **searched_in**: where the street was searched (`settlement`, `community`, `region`)
- **type_check**: whether SPR's street type (Փ., ՆՐԲ. …) agrees with the cadastre's
- **address_pick**: `single` = one cadastre id; `by_object_type` = the SPR house type chose between several
- **object_type_check**: whether the cadastre object (house, apartment, land plot) fits the SPR house type
- **data_completeness**: % of the 6 SPR address parts that are filled
- **earlier_match_type**: the previous mapping's verdict (`AMBIGUOUS` or blank)
""")
