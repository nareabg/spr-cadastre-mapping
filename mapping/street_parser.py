"""
Turns street text into a comparable structure.

    Street(main, main_type, sub, sub_type)
      main / sub : tuple of folded tokens, ordinal suffixes removed
                   ('8-րդ' -> '8', 'Ա-1' -> 'ա 1')
      *_type     : canonical type word ('փողոց', 'նրբանցք', ...) or None

Cadastre already stores the parts in separate columns (cadastre_street()).
SPR stores one free-text string, so parse_spr_street() returns one or more
READINGS of it. Readings, in the order they are tried:

  as_written            'ՍՈՍԵՅԻ Փ. 1 ՆՐԲ.'  -> main 'սոսեյի' փողոց, sub '1' նրբանցք
  neighbourhood_street  'Ա2 ԹՂՄ. 9 Փ.'      -> main 'ա 2 թաղամասի 9' փողոց
  lane_of_street        'ԱՅԳԵՍՏԱՆ 2 ՆՐԲ.'   -> main 'այգեստան' (no type), sub '2' նրբանցք
  without_place_prefix  'Ք. ԱՊԱՐԱՆ ԲԱՂՐԱՄՅԱՆ Փ.' -> 'ԲԱՂՐԱՄՅԱՆ Փ.' when the prefix is the place itself
  without_house_numbers 'ԱՆԴՐԱՆԻԿԻ 15'      -> main 'անդրանիկի' (only when no type word is written)
"""
from dataclasses import dataclass
from .normalize import fold
from . import nare_rules

# Abbreviation -> type word. Extends the notebook's TYPE_MAP with the
# abbreviations that occur in the SPR file (ՄԿՐՇ., ԶՆԳՎ., Մ/Շ ...). These
# are spellings of the TYPE, not street-name aliases.
TYPE_WORDS = {
    "փ": "փողոց", "փող": "փողոց", "փողոց": "փողոց",
    "նրբ": "նրբանցք", "նրբանցք": "նրբանցք",
    "փկղ": "փակուղի", "փակուղի": "փակուղի",
    "թղմ": "թաղամաս", "թաղ": "թաղամաս", "թաղամաս": "թաղամաս",
    "խճղ": "խճուղի", "խճ": "խճուղի", "խճուղի": "խճուղի",
    "պող": "պողոտա", "պողոտա": "պողոտա", "պ": "պողոտա",
    "անցղ": "անցուղի", "անցուղի": "անցուղի",
    "մկրշ": "միկրոշրջան", "միկրոշրջան": "միկրոշրջան",
    "զնգվ": "զանգված", "զանգված": "զանգված",
    "շարք": "շարք", "մայրուղի": "մայրուղի", "հրապարակ": "հրապարակ", "հր": "հրապարակ",
    "տարածք": "տարածք", "այգետարածք": "այգետարածք", "տեղամաս": "տեղամաս", "հանդամաս": "հանդամաս",
}
# One/two-letter abbreviations that are also common initials (Պ. Սևակ, ՀՐ. Ներսիսյան).
# They count as a type only when nothing name-like follows them.
AMBIGUOUS_TYPE_TOKENS = {"պ", "հր"}
SUB_TYPES = {"նրբանցք", "փակուղի", "անցուղի", "շարք"}
AREA_TYPES = {"թաղամաս", "միկրոշրջան", "զանգված"}
AREA_GENITIVE = {"թաղամաս": "թաղամասի", "միկրոշրջան": "միկրոշրջանի", "զանգված": "զանգվածի"}
ORDINAL_SUFFIX = {"րդ", "ին", "նդ", "ամ", "երորդ"}
GENITIVE_SUFFIX = {"ի", "յի"}                 # written after a hyphen: 'Ա-ի'
# Words too common to identify a street on their own (extra_words rule).
GENERIC_WORDS = {"նոր", "հին", "վերին", "ներքին", "մեծ", "փոքր", "ավան", "այգեգործական", "տարածք",
                 "թաղամաս", "թաղ", "կենտրոն", "կենտրոնական", "փողոց", "գյուղ", "բանավան", "զանգված",
                 "միկրոշրջան", "հատված", "շարք"}
# Notebook HOUSE_TOKEN: words that start the house / apartment part of the text.
HOUSE_WORDS = {"տուն", "բն", "տնակ", "շ", "շին", "շինություն", "շենք", "հնկց", "հանր", "հանրակացարան", "ակումբ"}


@dataclass(frozen=True)
class Street:
    main: tuple
    main_type: object
    sub: tuple = ()
    sub_type: object = None

    def text(self) -> str:
        p = [" ".join(self.main), self.main_type or "", " ".join(self.sub), self.sub_type or ""]
        return " | ".join(x for x in p if x)


def has_digit(t: str) -> bool:
    return any(ch.isdigit() for ch in t)


def tokens(text) -> list:
    """Folded tokens. Ordinal suffixes after a number are dropped ('8 րդ' -> '8')
    and a single letter touching a number is glued to it ('Ա-1' -> 'ա1',
    '110-Ա' -> '110ա'), because there the letter is part of the number, not an
    initial."""
    folded = fold(text).split()
    raw = []
    for i, t in enumerate(folded):
        if t in ORDINAL_SUFFIX and raw and raw[-1].isdigit():
            continue
        nxt = folded[i + 1] if i + 1 < len(folded) else None
        if t in GENITIVE_SUFFIX and raw and (nxt is None or nxt in TYPE_WORDS or has_digit(nxt)):
            continue                                  # 'Գարեգին Ա-ի' -> 'գարեգին ա'
        raw.append(t)
    out, i = [], 0
    while i < len(raw):
        t = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        if len(t) == 1 and not t.isdigit() and t not in TYPE_WORDS and nxt is not None and nxt.isdigit() \
                and not (out and out[-1].isdigit()):
            out.append(t + nxt)                       # 'ա 1' -> 'ա1'
            i += 2
            continue
        if t.isdigit() and nxt is not None and len(nxt) == 1 and not nxt.isdigit() and nxt not in TYPE_WORDS:
            out.append(t + nxt)                       # '110 ա' -> '110ա'
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def split_name(main) -> tuple:
    """-> (words, initials, codes).
    initials : single letters followed by a word ('Ա. Խաչատրյան')
    codes    : numbers and stand-alone letters ('5', '110ա', 'ա1', the 'Ե' in
               'Դիմացի Ե'); they identify the street and are compared exactly
    words    : everything else"""
    words, initials, codes = [], [], []
    for i, t in enumerate(main):
        if has_digit(t):
            codes.append(t)
        elif len(t) == 1:
            nxt = main[i + 1] if i + 1 < len(main) else None
            if nxt is not None and not has_digit(nxt) and len(nxt) > 1:
                initials.append(t)
            else:
                codes.append(t)
        else:
            words.append(t)
    return words, initials, codes


def canon_type(x):
    t = " ".join(tokens(x))
    return TYPE_WORDS.get(t, t) if t else None


def cadastre_street(name, typ, sub_name, sub_typ) -> Street:
    return Street(tuple(tokens(name)), canon_type(typ), tuple(tokens(sub_name)), canon_type(sub_typ))


# ------------------------------------------------------------------ SPR side
def _segments(toks):
    """Split tokens into (name_tokens, type) segments; leftovers are returned."""
    segs, cur = [], []
    for i, t in enumerate(toks):
        typ = TYPE_WORDS.get(t)
        if nare_rules.PASSAGE_AFTER_STREET_TYPE and typ in SUB_TYPES and not cur and segs:
            # unnamed lane / passage right after the street type (nare_rules #1):
            # 'ՔԱՆԱՔԵՌ 12 Փ. ԱՆՑՂ.' = cadastre 'Քանաքեռի 12-րդ փողոց' + blank-name 'անցուղի'
            segs.append(((), typ))
        elif typ and cur:
            if t in AMBIGUOUS_TYPE_TOKENS:
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is not None and not (nxt.isdigit() or nxt in TYPE_WORDS or nxt in HOUSE_WORDS):
                    cur.append(t)
                    continue
            segs.append((tuple(cur), typ))
            cur = []
        else:
            cur.append(t)
    return segs, cur


def _cut_house(toks):
    """Name part before the first house word (notebook parse_street)."""
    for i, t in enumerate(toks):
        if t in HOUSE_WORDS:
            return toks[:i], toks[i:]
    return toks, []


def _tail_kind(tail) -> str:
    """What is left after the street part.
      none   : nothing
      house  : starts with a house word ('ՏՈՒՆ58', 'ԲՆ 4') -> safe to ignore
      digits : only numbers / single letters ('15', '15 Ա') -> must equal the
               row's house number, checked later per row
      other  : more street words or types -> the reading is not used"""
    if not tail:
        return "none"
    if tail[0] in HOUSE_WORDS:
        return "house"
    if all(has_digit(t) or len(t) == 1 for t in tail):
        return "digits"
    return "other"


def _flat(segs):
    out = []
    for n, t in segs:
        out += list(n) + [t]
    return tuple(out)


def _readings_from_tokens(toks):
    """[(reading, Street, tail_tokens)] for one token list."""
    out = []
    segs, rest = _segments(toks)
    rest = tuple(rest)
    if segs:
        n0, t0 = segs[0]
        if len(segs) > 1 and (segs[1][1] in SUB_TYPES):
            out.append(("as_written", Street(n0, t0, segs[1][0], segs[1][1]), _flat(segs[2:]) + rest))
        else:
            out.append(("as_written", Street(n0, t0), _flat(segs[1:]) + rest))
        if t0 in AREA_TYPES and len(segs) > 1:
            n1, t1 = segs[1]
            main = n0 + (AREA_GENITIVE[t0],) + n1
            if len(segs) > 2 and segs[2][1] in SUB_TYPES:
                out.append(("neighbourhood_street", Street(main, t1, segs[2][0], segs[2][1]), _flat(segs[3:]) + rest))
            else:
                out.append(("neighbourhood_street", Street(main, t1), _flat(segs[2:]) + rest))
        if t0 in SUB_TYPES and len(n0) >= 2 and has_digit(n0[-1]) and not has_digit(n0[0]):
            out.append(("lane_of_street", Street(n0[:-1], None, (n0[-1],), t0), _flat(segs[1:]) + rest))
    else:
        name, tail = _cut_house(list(rest))
        if name:
            out.append(("as_written", Street(tuple(name), None), tuple(tail)))
            if any(has_digit(t) for t in name) and not has_digit(name[0]):
                i = next(k for k, t in enumerate(name) if has_digit(t))
                out.append(("without_house_numbers", Street(tuple(name[:i]), None), tuple(name[i:]) + tuple(tail)))
    return out


def parse_spr_street(text, place_names=()) -> list:
    """All usable readings of an SPR street string: [(reading, Street, tail_kind, tail)].
    place_names: folded settlement / community names of the row, used to
    detect a leading place prefix ('Ք. ԱՊԱՐԱՆ ...')."""
    toks = tokens(text)
    if not toks:
        return []
    readings = _readings_from_tokens(toks)
    for place in place_names:
        p = place.split()
        if not p:
            continue
        start = 1 if toks[0] in ("ք", "գ") and len(toks) > 1 else 0
        if toks[start:start + len(p)] == p and len(toks) > start + len(p):
            rest = toks[start + len(p):]
            if TYPE_WORDS.get(rest[0]):        # 'ԱՊԱՐԱՆ Փ.' is a street named after the place
                continue
            for name, st, tail in _readings_from_tokens(rest):
                if name != "without_house_numbers":
                    readings.append(("without_place_prefix", st, tail))
            break
    seen, out = set(), []
    for name, st, tail in readings:
        kind = _tail_kind(tail)
        if st.main and kind != "other" and st not in seen:
            seen.add(st)
            out.append((name, st, kind, " ".join(tail)))
    return out


def is_house_only(text) -> bool:
    """Street text that holds no street name (blank, 'ՏՆԱԿ', 'ՏՈՒՆ6')."""
    toks = tokens(text)
    name, _ = _cut_house(toks)
    return not any(not has_digit(t) for t in name) and not any(TYPE_WORDS.get(t) for t in toks[1:])
