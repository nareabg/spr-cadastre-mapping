"""
Finds the cadastre street for one SPR street text inside one geography.

Search order (the first hit decides, nothing after it is tried):
  for each SCOPE   (settlement -> community; region only when no community)
    for each RULE  (1 exact ... 7 fuzzy)
      for each READING of the text (as_written, neighbourhood_street, ...)

Rules 1-6 compare cleaned keys; only rule 7 uses a similarity score.
Numbers are never fuzzy: every rule requires the street's numbers
(digits in the name, in the same order, and the lane number) to be identical.

What a hit becomes:
  found          exactly one cadastre street, type compatible
  type_mismatch  name found, but both sides have a type and they differ
  ambiguous      the rule found several different cadastre streets
  suggested      fuzzy only: score 80-90, or >= 90 but too close to the runner-up
  not_found      nothing in any scope
"""
from collections import defaultdict
from rapidfuzz import fuzz

from .aliases import GLOBAL_STREET_ALIASES, SETTLEMENT_STREET_ALIASES, STREET_TYPO_FIXES
from . import nare_rules
from .normalize import colleague_norm, collapse_doubles, stem, fold, apply_typo_fixes, place_key
from .street_parser import parse_spr_street, split_name, Street, GENERIC_WORDS, cadastre_street

RULES = ["exact", "alias", "double_letter", "word_form", "extra_words", "initial_for_name", "fuzzy"]
STRONG_RULES = {"exact", "alias", "double_letter", "word_form"}
STRONG_READINGS = {"as_written", "neighbourhood_street"}

FUZZY_MAP_SCORE = 90      # >= this and clear of the runner-up -> found
FUZZY_MARGIN = 4          # points ahead of the next different street
FUZZY_SUGGEST_SCORE = 80  # >= this -> suggested (review), never mapped
FUZZY_MIN_LETTERS = 5     # shorter names are never mapped by fuzzy


_GENERIC_STEMS = {stem(w) for w in GENERIC_WORDS}


def _word_key(w):
    """A word as the matcher compares it (Feat.words): folded, no doubled letters, no ending."""
    return stem(collapse_doubles(fold(w).replace(" ", "")))


# nare_rules #7: pairs of names that are never the same street
_NEVER_SAME = [(_word_key(a), _word_key(b)) for a, b in nare_rules.NEVER_SAME_NAMES]
# nare_rules #8: SPR words that switch fuzzy off
_NO_FUZZY = {fold(w) for w in nare_rules.NO_FUZZY_WORDS}


def _never_same(q_words, f_words):
    qs, fs = set(q_words), set(f_words)
    for a, b in _NEVER_SAME:
        if (a in qs and b in fs and a not in fs) or (b in qs and a in fs and b not in fs):
            return True
    return False


# ----------------------------------------------------------------- features
class Feat:
    """Pre-computed comparison keys of one Street."""
    __slots__ = ("street", "exact", "dbl", "wf", "words", "initials", "digits", "sub_key", "letters")

    def __init__(self, st: Street):
        self.street = st
        sub_wf = (tuple(stem(t) for t in st.sub), st.sub_type)
        self.exact = (st.main, st.sub, st.sub_type)
        self.dbl = (tuple(collapse_doubles(t) for t in st.main), tuple(collapse_doubles(t) for t in st.sub), st.sub_type)
        self.wf = (tuple(stem(t) for t in st.main),) + sub_wf
        words, initials, codes = split_name(st.main)
        self.words = tuple(stem(collapse_doubles(t)) for t in words)
        self.initials = frozenset(initials)
        self.digits = tuple(codes)                  # numbers and code letters: exact, in order
        self.sub_key = sub_wf
        self.letters = " ".join(self.words)


class Pool:
    """All cadastre streets of one scope, indexed per rule."""

    def __init__(self, entries):
        self.entries = entries                     # list of (uid, Feat)
        self.by_exact, self.by_dbl, self.by_wf = defaultdict(list), defaultdict(list), defaultdict(list)
        self.by_shape = defaultdict(list)          # (digits, sub) -> entries, for scan rules
        for uid, f in entries:
            self.by_exact[f.exact].append((uid, f))
            self.by_dbl[f.dbl].append((uid, f))
            self.by_wf[f.wf].append((uid, f))
            self.by_shape[(f.digits, f.sub_key)].append((uid, f))


# ----------------------------------------------------------------- rules
def _rule_subset(q: Feat, pool: Pool):
    """extra_words: every word of the SPR name is in the cadastre name, which has more words."""
    qs = set(q.words)
    if not (qs - _GENERIC_STEMS):
        return []                           # 'ՆՈՐ ԹԱՂԱՄԱՍ' alone proves nothing
    out = []
    for uid, f in pool.by_shape[(q.digits, q.sub_key)]:
        fs = set(f.words)
        if qs < fs and all(i in f.initials or any(w[0] == i for w in f.words) for i in q.initials):
            out.append((uid, f))
    return out


def _rule_initials(q: Feat, pool: Pool):
    """initial_for_name: same words except that one side writes a name as its
    initial ('Ք.' vs 'ՔԸՐՔ'), or one side omits the initial. Two different
    written initials never match."""
    if not q.words:
        return []
    out = []
    qs = set(q.words)
    for uid, f in pool.by_shape[(q.digits, q.sub_key)]:
        fs = set(f.words)
        if qs == fs:
            if q.initials and f.initials and q.initials != f.initials:
                continue
            if q.initials == f.initials:
                continue                      # identical -> would have been word_form
            out.append((uid, f))
        elif fs < qs:
            extra = qs - fs
            if f.initials and len(extra) == len(f.initials) and {w[0] for w in extra} == set(f.initials) \
                    and not (q.initials - f.initials):
                out.append((uid, f))
        elif qs < fs:
            extra = fs - qs
            if q.initials and len(extra) == len(q.initials) and {w[0] for w in extra} == set(q.initials) \
                    and not (f.initials - q.initials):
                out.append((uid, f))
    return out


def _rule_fuzzy(q: Feat, pool: Pool):
    """Similarity of the letters only. Numbers, lane and written initials must agree."""
    if not q.words:
        return []
    scored = []
    for uid, f in pool.by_shape[(q.digits, q.sub_key)]:
        if not f.words:
            continue
        if q.initials and f.initials and q.initials != f.initials:
            continue
        if _never_same(q.words, f.words):
            continue
        sc = fuzz.token_sort_ratio(q.letters, f.letters)
        if sc >= FUZZY_SUGGEST_SCORE:
            scored.append((sc, uid, f))
    scored.sort(key=lambda x: -x[0])
    return scored


# ----------------------------------------------------------------- decision
# street = avenue: nare_rules #4 (never 'high'; fuzzy + avenue is only suggested)
EQUIVALENT_TYPES = {frozenset({"փողոց", "պողոտա"})} if nare_rules.STREET_EQUALS_AVENUE else set()


def _type_filter(cands, spr_type):
    """-> (kept candidates, type_check)."""
    if spr_type is None:
        return cands, "issue has no type"
    same = [c for c in cands if c[1].street.main_type == spr_type]
    if same:
        return same, "same type"
    equiv = [c for c in cands if frozenset({c[1].street.main_type, spr_type}) in EQUIVALENT_TYPES]
    if equiv:
        return equiv, "street = avenue"
    untyped = [c for c in cands if c[1].street.main_type is None]
    if untyped:
        return untyped, "cadastre has no type"
    return [], "different type"


def _decide(cands, spr_type):
    kept, tcheck = _type_filter(cands, spr_type)
    if not kept:
        return "type_mismatch", sorted({u for u, _ in cands}), tcheck
    uids = sorted({u for u, _ in kept})
    return ("found" if len(uids) == 1 else "ambiguous"), uids, tcheck


# ----------------------------------------------------------------- matcher
class StreetMatcher:
    def __init__(self, catalog):
        """catalog: iterable of (street_uid, region_k, community_k, settlement_k, Street)."""
        groups = {"settlement": defaultdict(list), "community": defaultdict(list), "region": defaultdict(list)}
        for uid, r, c, s, st in catalog:
            e = (uid, Feat(st))
            groups["settlement"][(r, c, s)].append(e)
            groups["community"][(r, c)].append(e)
            groups["region"][(r,)].append(e)
        self.pools = {lvl: {k: Pool(v) for k, v in d.items()} for lvl, d in groups.items()}
        self.street_alias = defaultdict(dict)
        for sett, old, new in SETTLEMENT_STREET_ALIASES + nare_rules.NARE_RENAMES:   # later rows win
            self.street_alias[place_key(sett)][colleague_norm(old)] = new
        # nare_rules #10 / #11: reviewed pairs -> cadastre street_uid, per settlement and per community
        uid_of = {(c, s, st): uid for uid, r, c, s, st in catalog}
        self.nare_alias, self.nare_alias_comm = {}, defaultdict(set)
        for c, s, t, st in nare_rules.NARE_STREET_ALIASES:
            uid = uid_of.get((place_key(c), place_key(s), cadastre_street(*st)))
            if uid is not None:
                self.nare_alias[(place_key(c), place_key(s), fold(t))] = uid
                self.nare_alias_comm[(place_key(c), fold(t))].add(uid)
        self.nare_block, self.nare_block_comm = defaultdict(set), defaultdict(set)
        for c, s, t, st in nare_rules.NARE_NOT_SAME:
            uid = uid_of.get((place_key(c), place_key(s), cadastre_street(*st)))
            if uid is not None:
                self.nare_block[(place_key(c), place_key(s), fold(t))].add(uid)
                self.nare_block_comm[(place_key(c), fold(t))].add(uid)

    # -- helpers
    def _alias_text(self, text, settlement_k):
        up = str(text).strip().upper()
        if up in GLOBAL_STREET_ALIASES:
            return GLOBAL_STREET_ALIASES[up]
        amap = self.street_alias.get(settlement_k)
        if amap:
            return amap.get(colleague_norm(text))
        return None

    def _index_hits(self, readings, pool, attr, index):
        for reading, st, kind, tail in readings:
            hits = index.get(getattr(Feat(st), attr))
            if hits:
                yield reading, st, kind, tail, hits

    def match(self, scopes, text, place_names=()):
        """scopes: [(level, key)] in search order. Returns a result dict."""
        readings = parse_spr_street(text, place_names)
        base = {"street_status": None, "street_uid": None, "candidate_uids": None, "match_method": None,
                "text_reading": None, "searched_in": None, "scope_widened": None, "type_check": None,
                "similarity_score": None, "runner_up_score": None, "tail_kind": None, "tail": None,
                "parsed_street": readings[0][1].text() if readings else None}
        if not readings:
            return {**base, "street_status": "no_readings"}

        for si, (level, key) in enumerate(scopes):
            pool = self.pools[level].get(key)
            if pool is None:
                continue
            res = self._match_in_pool(text, readings, pool, key, place_names, widened=si > 0)
            if res is not None:
                return {**base, **res, "searched_in": level, "scope_widened": si > 0}
        return {**base, "street_status": "not_found"}

    def _match_in_pool(self, text, readings, pool, scope_key, place_names, widened=False):
        def hit(status, uids, tcheck, rule, reading, kind, tail, score=None, runner=None):
            return {"street_status": status, "street_uid": uids[0] if status == "found" else None,
                    "candidate_uids": uids, "match_method": rule, "text_reading": reading,
                    "type_check": tcheck, "similarity_score": score, "runner_up_score": runner,
                    "tail_kind": kind, "tail": tail}

        # nare_rules #11: a pair marked as NOT the same street is removed from this search
        ft = fold(text)
        if len(scope_key) == 3:
            blocked = self.nare_block.get((scope_key[1], scope_key[2], ft))
        elif len(scope_key) == 2:
            blocked = self.nare_block_comm.get((scope_key[1], ft))
        else:
            blocked = None
        if blocked:
            pool = Pool([(u, f) for u, f in pool.entries if u not in blocked])

        # 1 exact
        for reading, st, kind, tail, hits in self._index_hits(readings, pool, "exact", pool.by_exact):
            return hit(*_decide(hits, st.main_type), "exact", reading, kind, tail)

        # 2 alias: Nare's reviewed pairs first (exact text in this settlement), then colleagues' renames
        settlement_k = scope_key[2] if len(scope_key) == 3 else None
        if settlement_k is not None:
            targets = {self.nare_alias[k]} if (k := (scope_key[1], settlement_k, ft)) in self.nare_alias else set()
        elif len(scope_key) == 2 and not widened:       # whole community / city: Yerevan row without district
            targets = self.nare_alias_comm.get((scope_key[1], ft), set())
        else:
            targets = set()
        uids = sorted(u for u, f in pool.entries if u in targets)
        if len(uids) == 1:
            r0 = readings[0]
            return hit("found", uids, "set by alias", "alias", r0[0], r0[2], r0[3])
        alias_text = self._alias_text(text, settlement_k)
        if alias_text:
            orig_type = readings[0][1].main_type
            al = parse_spr_street(alias_text, place_names)
            for attr, index in (("exact", pool.by_exact), ("wf", pool.by_wf)):
                for reading, st, kind, tail, hits in self._index_hits(al, pool, attr, index):
                    return hit(*_decide(hits, st.main_type or orig_type), "alias", reading, kind, tail)

        # 3 double_letter (notebook typo list, then doubled letters collapsed)
        fixed = apply_typo_fixes(fold(text), STREET_TYPO_FIXES)
        if fixed != fold(text):
            for attr, index in (("exact", pool.by_exact), ("wf", pool.by_wf)):
                for reading, st, kind, tail, hits in self._index_hits(parse_spr_street(fixed, place_names), pool, attr, index):
                    return hit(*_decide(hits, st.main_type), "double_letter", reading, kind, tail)
        for reading, st, kind, tail, hits in self._index_hits(readings, pool, "dbl", pool.by_dbl):
            return hit(*_decide(hits, st.main_type), "double_letter", reading, kind, tail)

        # 4 word_form (genitive endings)
        for reading, st, kind, tail, hits in self._index_hits(readings, pool, "wf", pool.by_wf):
            return hit(*_decide(hits, st.main_type), "word_form", reading, kind, tail)

        # 5 extra_words, 6 initial_for_name
        for rule, fn in (("extra_words", _rule_subset), ("initial_for_name", _rule_initials)):
            for reading, st, kind, tail in readings:
                hits = fn(Feat(st), pool)
                if hits:
                    return hit(*_decide(hits, st.main_type), rule, reading, kind, tail)

        # 7 fuzzy (never for dormitory texts, nare_rules #8)
        if _NO_FUZZY & set(ft.split()):
            return None
        for reading, st, kind, tail in readings:
            q = Feat(st)
            scored = _rule_fuzzy(q, pool)
            if not scored:
                continue
            kept, tcheck = _type_filter([(u, f) for _, u, f in scored], st.main_type)
            if not kept:
                uids = sorted({u for sc, u, f in scored if sc >= FUZZY_MAP_SCORE})
                if uids:
                    return hit("type_mismatch", uids, tcheck, "fuzzy", reading, kind, tail, scored[0][0])
                continue
            keep_ids = {id(f) for _, f in kept}
            ranked = [(sc, u, f) for sc, u, f in scored if id(f) in keep_ids]
            best_sc, best_uid, best_f = ranked[0]
            runner = next((sc for sc, u, f in ranked[1:] if f.letters != best_f.letters), None)
            margin_ok = runner is None or best_sc - runner >= FUZZY_MARGIN
            same_best = sorted({u for sc, u, f in ranked if sc == best_sc})
            if len(same_best) > 1:
                return hit("ambiguous", same_best, tcheck, "fuzzy", reading, kind, tail, best_sc, runner)
            # a bare word with no type, initial or number ('ԵՐԵՎԱՆ') is never mapped by fuzzy
            bare = st.main_type is None and not st.sub and not q.initials and not q.digits and len(q.words) == 1
            # similar spelling AND street-vs-avenue: two doubts -> only suggested
            if best_sc >= FUZZY_MAP_SCORE and margin_ok and not bare and tcheck != "street = avenue" \
                    and len(q.letters.replace(" ", "")) >= FUZZY_MIN_LETTERS:
                return hit("found", [best_uid], tcheck, "fuzzy", reading, kind, tail, best_sc, runner)
            return hit("suggested", [best_uid], tcheck, "fuzzy", reading, kind, tail, best_sc, runner)
        return None
