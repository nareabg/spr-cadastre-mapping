"""
Text normalisation shared by every step. The same function always runs on
both sides (SPR and cadastre), so differences in spelling style cancel out.

Functions
  fold(x)            base form for street text (notebook nrm() + colleague fixes)
  place_key(x)       geography key (notebook gnrm() + colleague filler words)
  norm_place(x)      the colleagues' alias lookup key (spr.norm_place), used
                     ONLY to look up aliases.GEO_ALIASES / COMMUNITY_ALIASES
  colleague_norm(x)  port of spr.addr_norm_component, used ONLY to look up
                     aliases.SETTLEMENT_STREET_ALIASES
  number_key(x)      house / apartment number key (spr.addr_norm_number + fixes)
  stem(token)        genitive ending removal (notebook stem())
"""
import re
import pandas as pd

# ---------------------------------------------------------------- basics
_DASHES = "-֊‐‑‒–—―"
_PUNCT = re.compile(r"[.,/\\«»<>\"'`՛՝։․|:;()\[\]_+*!?{}=~^&%$#@" + re.escape(_DASHES) + r"]")
_SPACES = re.compile(r"\s+")
_LETTER_DIGIT = re.compile(r"(?<=\d)(?=[^\d\s])|(?<=[^\d\s])(?=\d)")
# Abbreviations that contain a slash; must be read before '/' becomes a space.
_SLASH_ABBR = {"թ/ղ": " թաղամաս ", "մ/շ": " միկրոշրջան "}
# Colleagues' literal folds (AddressNormalizer._norm / spr.addr_norm_component),
# in lower case. Applied to both sides.
_LITERAL_FOLDS = {"անտառավան": "անտառ", "աշոտավան բանավան": "աշոտավան", "արփիկ բանավան": "արփիկ"}


def is_blank(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x)) or str(x).strip() == ""


def fold(x) -> str:
    """Lower case, և -> եվ, ոի -> ու typo, lone ւ -> ու, punctuation -> space,
    split letters from digits ('ա8' -> 'ա 8'), collapse spaces."""
    if is_blank(x):
        return ""
    s = str(x).lower()
    s = s.replace("և", "եվ").replace("ոի", "ու")
    s = re.sub(r"(?<!ո)ւ", "ու", s)
    for k, v in _SLASH_ABBR.items():
        s = s.replace(k, v)
    s = _PUNCT.sub(" ", s)
    s = _LETTER_DIGIT.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    for k, v in _LITERAL_FOLDS.items():
        s = s.replace(k, v)
    return s


# ---------------------------------------------------------------- geography
_PLACE_FILLERS = {"մարզ", "համայնք", "քաղաք", "գյուղ"}       # colleague filler words
_PAREN = re.compile(r"\s*[(（][^)）]*[)）]?")                    # spr.addr_key_settlement


def place_key(x) -> str:
    """Region / community / settlement key. Drops '(...)' qualifiers, a leading
    Գ./Ք./ԱՎ. marker and administrative filler words."""
    if is_blank(x):
        return ""
    s = _PAREN.sub(" ", str(x))
    toks = fold(s).split()
    while toks and toks[0] in ("գ", "ք", "ավ") and len(toks) > 1:
        toks = toks[1:]
    toks = [t for t in toks if t not in _PLACE_FILLERS]
    return " ".join(toks)


def norm_place(x) -> str:
    """spr.norm_place(): lower, trim, և -> եվ, lone ւ -> ու. Alias keys only."""
    if is_blank(x):
        return ""
    s = str(x).strip().lower().replace("և", "եվ")
    return re.sub(r"(?<!ո)ւ", "ու", s)


# ---------------------------------------------------------------- colleague key
_SUFFIXES = ['ԽՃՂ', 'ԽՃ', 'ՐԴ', 'ԻՆ', 'ՆՐԲ', 'ՇԱՐՔ', 'ԱՆՑՂ', 'ՓԿՂ', 'ԲՆ', 'ՃՂ', 'ՊՈՂ', 'ԱՎ', 'ՃԱՄԲ',
             'ՏՈՒՆ', 'ՀՈՂ', 'ՓԱԿ', 'ԹՂՄ', 'ԹԱՂԱՄԱՍԻ', 'ԹԵԼԱ', 'ՃԱՆ', 'ՇՐՋ', 'Թ/Ղ', 'Փ', 'Պ']
_SUFFIX_RES = [re.compile(r"(?<![Ա-Ֆա-ֆ0-9])" + re.escape(s) + r"\.?") for s in _SUFFIXES]


def colleague_norm(x) -> str:
    """Python port of spr.addr_norm_component (01_hasce_address_staged/00).
    Used only as the lookup key for SETTLEMENT_STREET_ALIASES."""
    if is_blank(x):
        return ""
    t = str(x).strip()
    if t.upper() in ("Խ. ՀԱՅՐԻԿ", "ԽՐԻՄՅԱՆ ՀԱՅՐԻԿ"):
        t = "ԽՐԻՄՅԱՆ ՀԱՅՐԻԿԻ"
    t = t.upper().replace("ԵՎ", "և")
    t = t.replace("ԱՆՏԱՌԱՎԱՆ", "ԱՆՏԱՌ").replace("ԱՇՈՏԱՎԱՆ ԲԱՆԱՎԱՆ", "ԱՇՈՏԱՎԱՆ").replace("ԱՐՓԻԿ ԲԱՆԱՎԱՆ", "ԱՐՓԻԿ")
    t = re.sub(r"(?<![Ա-Ֆ])ՀՐ(?![Ա-Ֆ])", "ՀՐԱՊԱՐԱԿ", t)
    t = re.sub("ՄՈՒՇ[" + re.escape(_DASHES) + "]2", "ՄՈՒՇ 2", t)
    t = _ORDINAL.sub(r"\1", t)
    for w in ("ՄԱՐԶ", "ՀԱՄԱՅՆՔ", "ՔԱՂԱՔ", "ԳՅՈՒՂ", "ՇԱՐԼ", "ՇԱՌԼ", "ՕՂԱԿ"):
        t = re.sub(r"(?<![Ա-Ֆ])" + w + r"(?![Ա-Ֆ])", "", t)
    t = re.sub(r"(?<![Ա-Ֆ])[ԳՔ][.․։]", "", t)
    t = " " + t + " "
    for _ in range(2):
        t = re.sub(r" [Ա-Ֆ]\.? ", "  ", t)
    for rx in _SUFFIX_RES:
        t = rx.sub("", t)
    t = re.sub(r"ԱՆՑՂ\.?", "", t)
    t = re.sub(r"[^0-9A-Za-zԱ-Ֆա-ֆև\s/-]", "", t)
    t = " " + _SPACES.sub(" ", t).strip() + " "
    t = t.replace("Ի ", " ").replace("ՅԻ ", " ").replace("ՈՒ ", " ")
    return _SPACES.sub(" ", t).strip()


# ---------------------------------------------------------------- numbers
_ORDINAL = re.compile(r"(\d+)[" + re.escape(_DASHES) + r"]?(ՐԴ|ՆԴ|ԻՆ|ԱՄ)\.?")


def number_key(x) -> str:
    """House / apartment key. Upper case, ordinals -> digits, '\\' -> '/',
    spaces removed, keeps letters, digits, '/', '-' and ','.
    Two numbers are the same only if their keys are identical (no fuzziness)."""
    if is_blank(x):
        return ""
    s = str(x).strip().upper().replace("ԵՎ", "և").replace("\\", "/")
    s = _ORDINAL.sub(r"\1", s)
    s = re.sub(r"[^0-9A-ZԱ-Ֆև/,\-]", "", s)
    return s.strip("/,-")


# ---------------------------------------------------------------- word forms
def stem(t: str) -> str:
    """Notebook stem(): strip genitive endings ի / յի (and colleague's ու)
    from words longer than 4 letters. Digits are never stemmed."""
    if any(ch.isdigit() for ch in t):
        return t
    while len(t) > 4 and t.endswith(("յի", "ի", "ու")):
        t = t[:-2] if t.endswith(("յի", "ու")) else t[:-1]
    return t


def collapse_doubles(t: str) -> str:
    """'երեվաանյան' -> 'երեվանյան'. Applied to both sides, so legitimate
    doubles (Հովհաննիսյան, Ռաֆֆի) still compare equal."""
    return re.sub(r"([ա-ֆ])\1+", r"\1", t)


def apply_typo_fixes(folded: str, fixes: dict) -> str:
    """Notebook rule: the first key contained in the text is replaced."""
    for wrong, right in fixes.items():
        if wrong in folded:
            return folded.replace(wrong, right)
    return folded
