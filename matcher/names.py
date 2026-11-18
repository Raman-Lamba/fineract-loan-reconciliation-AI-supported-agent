"""Whether a payer's name, as a bank wrote it, could be one borrower's: for names beyond first-name-then-surname.

Pure, standard library only, no network. Conservative on purpose: a tolerance is added only where the same
person is written differently (case, accents, word order, a cut-short field); anything weaker is no fit, and
the matcher then queues the line for a person. Two borrowers that both fit also go to a person (matcher.match).

A name is a list of words (`tokens`): normalised, a hyphenated word kept whole ("dupont-leclerc") and split
into parts when compared, so DUPONT LECLERC and DUPONTLECLERC both fit. A borrower's words split into given and
family names by NAME_ORDER: western = the first word is the given name (Fineract's clientName.partition(" ")),
family-first = the first word is the family name (East Asia, Hungary), auto = either, with no partial tolerance.

Rules of `fits` (one example each, borrower first, then the payer):
(a) every borrower word is in the payer, any order:            Jose Garcia Lopez / GARCIA LOPEZ JOSE
(b) the family name in full and the given name equal, a
    3+ letter prefix or an initial:                             Ramesh Kumar / R KUMAR
(c) the family name cut short to 2+ letters (a bank's field):   Ramesh Kumar / RAMESH KUM; run together: RAMESHKUMA
(d) an extra middle name or patronymic in the payer is allowed
    when the given name is in full and the family name whole,
    but not in the first place, where it is another given name: Ivan Sidorov / IVAN PETROVICH SIDOROV fits,
                                                                PYOTR IVAN SIDOROV does not
(e) a single-name person fits only exactly:                     Sukarno / SUKARNO fits, SUKARN does not
(f) a joint payer is split and each person tried:               Anita Nair / RAMESH KUMAR AND ANITA NAIR
(g) a company payer never fits a person:                        Ramesh Kumar / RAMESH KUMAR PVT LTD
Particles (van, der, de, bin, al...) are ignored inside a name: Jan van der Berg / JAN BERG, JAN VANDERBERG.
Titles at the start (Mr, Dr, Sri, Shri...) are dropped:         Ramesh Kumar / SHRI RAMESH KUMAR.
Scripts with no letter case (Arabic, Devanagari...) fit only by whole words (a, b and d with no prefix or
initial); scripts with no spaces between words (Chinese, Japanese, Korean, Thai) only as the whole name.
Two scripts never fit: nothing is transliterated (Ivan Sidorov / ИВАН СИДОРОВ is no fit).
Deliberately not handled: transliteration, nicknames, married or maiden names, MUELLER for Müller.
Design: docs/superpowers/specs/2026-11-18-names-design.md
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache

ORDERS = ("western", "family-first", "auto")
# shortcut: small lists from general knowledge, UNVERIFIED against real bank data; add words a real feed shows.
TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sri", "shri", "shree", "smt", "kum", "mme", "mlle", "herr",
          "frau", "sr", "sra", "srta"}
PARTICLES = {"van", "der", "den", "de", "la", "le", "del", "di", "da", "dos", "das", "bin", "binti", "ibn", "bint",
             "al", "el", "von", "zu", "ter", "ten"}
COMPANY = {"ltd", "llc", "inc", "gmbh", "sa", "ag", "bv", "sarl", "plc", "pvt", "limited", "ooo", "kk", "llp", "corp",
           "pte", "srl", "nv", "spa"}
STRIP_MARKS = ("LATIN", "GREEK")  # an accent on these is dropped (José = Jose); elsewhere a mark is part of a letter
UNSPACED = {"CJK", "HIRAGANA", "KATAKANA", "HANGUL", "THAI", "LAO", "KHMER", "MYANMAR"}
JOINT = re.compile(r"\s+(?:and|&)\s+|\s*[&/,]\s*", re.IGNORECASE)


def _script(ch: str) -> str:
    return unicodedata.name(ch, "?").split(" ")[0]


@lru_cache(maxsize=65536)
def normalise(text: str) -> str:
    """Lower case without accents, titles or punctuation: ' Mr. José  García-López ' -> 'jose garcia-lopez'."""
    out, strip = [], False
    for ch in unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).casefold()):
        if unicodedata.category(ch).startswith("M"):
            if strip:
                continue
        else:
            strip = _script(ch) in STRIP_MARKS
        out.append(ch)
    text = unicodedata.normalize("NFC", "".join(out))
    text = re.sub(r"['’‘`´ʼ]", "", text)  # O'Brien -> obrien
    text = "".join(ch if ch.isspace() or ch == "-" or unicodedata.category(ch)[0] in "LM" else " " for ch in text)
    words = [re.sub(r"-+", "-", word).strip("-") for word in text.split()]
    words = [word for word in words if word]
    while len(words) > 2 and words[0] in TITLES:  # a title is kept when it would leave one word: Sri Wahyuni
        words.pop(0)
    return " ".join(words)


@lru_cache(maxsize=65536)
def _tokens(name: str) -> tuple:
    return tuple(normalise(name).split())


def tokens(name: str) -> list[str]:
    """The words of a name, normalised: 'Anne-Marie Dupont-Leclerc' -> ['anne-marie', 'dupont-leclerc']."""
    return list(_tokens(name))


def of_loan(loan: dict) -> list[str]:
    """A loan's borrower as words: its name_tokens, or (a schedules file from before them) its first and last name."""
    return loan.get("name_tokens") or tokens(f"{loan['firstname']} {loan['lastname']}")


def parts(words) -> list[str]:
    return [part for word in words for part in word.split("-") if part]


def _particle(parts: list[str], i: int, keep_first: bool) -> bool:
    """Whether parts[i] is a particle with a name after it (van der berg: van, der). A last 'das' (an Indian
    surname) is not one; nor is a borrower's first word, which is their given name (Al Smith)."""
    return (parts[i] in PARTICLES and not (keep_first and i == 0)
            and any(p not in PARTICLES for p in parts[i + 1:]))


def _drop_particles(parts: list[str], keep_first: bool) -> list[str]:
    return [part for i, part in enumerate(parts) if not _particle(parts, i, keep_first)]


def is_company(words) -> bool:
    """A company suffix as a word (LTD, GMBH) or spelt out in single letters at the end (S.A. -> s a)."""
    flat = parts(words)
    letters = ""
    for part in reversed(flat):
        if len(part) != 1:
            break
        letters = part + letters
    return any(part in COMPANY for part in flat) or (len(letters) >= 2 and letters in COMPANY)


def _scripts(parts) -> frozenset:
    return frozenset(_script(ch) for part in parts for ch in part if unicodedata.category(ch).startswith("L"))


def _cased(parts) -> bool:
    return all(any(ch.upper() != ch for ch in part) for part in parts)


def readings(words, order: str) -> list[tuple]:
    """(given parts, family parts without particles, family parts as written) for each way NAME_ORDER reads a
    borrower of two or more words: western, the first word is the given name; family-first, the first is the family
    name; auto, both."""
    if len(words) < 2:
        return []
    marked = [(part, n) for n, word in enumerate(words) for part in word.split("-") if part]
    parts = [part for part, _ in marked]
    out = []
    for first_is_given in {"western": (True,), "family-first": (False,), "auto": (True, False)}[order]:
        given_word = (lambda n: n == 0) if first_is_given else (lambda n: n > 0)
        given = [p for i, (p, n) in enumerate(marked) if given_word(n) and not _particle(parts, i, True)]
        family = [p for i, (p, n) in enumerate(marked) if not given_word(n) and not _particle(parts, i, True)]
        out.append((given, family, [p for p, n in marked if not given_word(n)]))
    return out


def _given_fits(rest: list[str], given: list[str], first: int, exact: bool, whole_family: bool) -> bool:
    """Whether the payer's words left after the family name hold this given name (rules b and d).

    `first` is the index in `rest` of the payer's own first word, or -1 if the family name came first.
    """
    if not rest or not given:
        return False
    joined = "".join(given)
    if joined in rest or Counter(given) <= Counter(rest):
        extras = [i for i, t in enumerate(rest) if t not in given and t != joined]
        return not extras or (whole_family and first not in extras)
    if exact or len(rest) != 1:
        return False
    t = rest[0]
    return t == joined[:1] or (len(t) >= 3 and joined.startswith(t))


def _fits_reading(payer: list[str], given: list[str], family: list[str], family_full: list[str], exact: bool) -> bool:
    keys = {"".join(family), "".join(family_full)}
    for i in range(len(payer)):
        for j in range(i + 1, len(payer) + 1):
            run = "".join(payer[i:j])
            if len(run) < 2:  # one letter never stands for a family name
                continue
            whole = run in keys
            if not whole and (exact or not any(key.startswith(run) for key in keys)):
                continue
            rest = payer[:i] + payer[j:]
            if _given_fits(rest, given, 0 if i > 0 else -1, exact, whole):
                return True
    return False


def fits(payer, borrower, order: str = "western") -> bool:
    """Whether a payer (words, as `tokens` gives them) could be this borrower (words) under NAME_ORDER."""
    flat_p, flat_b = parts(payer), parts(borrower)
    if not flat_p or not flat_b or _scripts(flat_p) != _scripts(flat_b):
        return False  # different scripts: no transliteration, a person decides
    if _scripts(flat_p) & UNSPACED:
        return "".join(flat_p) == "".join(flat_b) or sorted(flat_p) == sorted(flat_b)
    company_p, company_b = is_company(flat_p), is_company(flat_b)
    if company_p or company_b:  # (g): a company fits only the same company, word for word
        plain = lambda parts: sorted(p for p in parts if p not in COMPANY and len(p) > 1)  # noqa: E731
        return company_b and plain(flat_p) == plain(flat_b)
    if len(borrower) == 1:  # (e)
        return flat_p == flat_b
    exact = order == "auto" or not _cased(flat_p + flat_b)
    for p, b in ((flat_p, flat_b), (_drop_particles(flat_p, False), _drop_particles(flat_b, True))):  # (a), (d)
        extra = Counter(p) - Counter(b)
        if not Counter(b) - Counter(p) and (not extra or p[0] not in extra):
            return True
    # The payer as written and without particles: DAS LAKSHM is Lakshmi Das (a surname, not the Portuguese das).
    for p in dict.fromkeys((tuple(flat_p), tuple(_drop_particles(flat_p, False)))):
        if len(p) == 1:  # (c) run together, in the order the name is stored: RAMESHKUMA
            head, tail, t = "".join(borrower[0].split("-")), "".join(parts(borrower[1:])), p[0]
            if not exact and t.startswith(head) and len(t) > len(head) and tail.startswith(t[len(head):]):
                return True
        elif any(_fits_reading(list(p), *reading, exact) for reading in readings(borrower, order)):
            return True
    return False


@lru_cache(maxsize=65536)
def payer_parts(text: str) -> tuple:
    """The people in a payer's name: two or more for a joint payer (A AND B, A & B, A / B, A, B), else one.

    Split only when every part has two or more words, so 'KUMAR, RAMESH' stays one person.
    """
    pieces = [tuple(normalise(piece).split()) for piece in JOINT.split(text) if piece.strip()]
    if len(pieces) >= 2 and all(len(piece) >= 2 for piece in pieces):
        return tuple(pieces)
    return (_tokens(text),)


def name_fits(payer: str, borrower, order: str = "western") -> bool:
    """Whether the payer's name as the bank wrote it (text) could be this borrower (words); (f) a joint payer
    fits a borrower if one of its people does."""
    if is_company(_tokens(payer)):
        return fits(list(_tokens(payer)), borrower, order)
    return any(fits(list(person), borrower, order) for person in payer_parts(payer))


def alike_keys(borrower, order: str = "western") -> set:
    """(family name, first initial) under each reading: two borrowers sharing one are look-alikes, which the matcher
    queues even when one fits in full (same surname, same first initial, same amount).

    family-first also keeps the western reading's keys: Fineract builds its display name given name first, so a book
    may hold either order, and a look-alike missed costs a wrong post where one too many costs only a queued line.
    Western keeps its own keys alone, exactly as before this module.
    """
    if len(borrower) < 2:
        return {("".join(parts(borrower)), "")}
    order = "western" if order == "western" else "auto"
    return {("".join(family), "".join(given)[:1]) for given, family, _ in readings(borrower, order)}
