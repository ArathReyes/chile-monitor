"""Parse BCCh monetary policy press releases (English). Pure functions.

Wording confirmed on the 45 statements May 2021 - Oct 2026 (fixtures: tests/fixtures/stmt_*.txt):
  header   "Tuesday, 14 December 2021" | "Tuesday, December 16, 2025" | "Thursday 13 May, 2021"
           | "Tuesday, 29th March 2022"
  decision "decided to raise the monetary policy interest rate (MPR) by 125 basis points, to 4%"
           | "decided to keep/hold/maintain the monetary policy interest rate at/in 5%"
  vote     "adopted by the unanimous vote of its members"
           | "adopted by all the Board members present"
           | "adopted with the (favorable) votes of Governor Costa and Board members Naudon
              and Soto. Vice-Governor Griffith-Jones voted for reducing it by 50 basis points."
Parsers raise ValueError when the structure isn't recognised (fail loudly).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], start=1)}  # fmt: skip
_WEEKDAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
# Garbled "Nota de prensa / Press Release" watermark before the weekday in the header.
_WATERMARK = re.compile(
    rf"^\s*(N\w*o\w*P\w*t\w*r\w*a\w*e\w*|Nota de prensa|Press Release)\b.*?(?=\b{_WEEKDAY}\b)",
    re.S,
)
_DAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?"
_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_PATTERNS = [
    re.compile(
        rf"{_DAY}\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(\d{{4}})", re.I
    ),  # 14 December 2021
    re.compile(rf"{_DAY}\s+{_MON}\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I),  # December 16, 2025
]
_ROLE = r"(?:Vice[-‐–]Governor|Governor|Board members?)"
_ACTION_SIGN = {"rais": 1, "hik": 1, "increas": 1, "lower": -1, "reduc": -1, "cut": -1,
                "decreas": -1, "keep": 0, "hold": 0, "maintain": 0}  # fmt: skip


def normalize(text: str) -> str:
    """Flatten whitespace, unify hyphens, drop the garbled 'Press Release' watermark."""
    t = " ".join(text.replace("‐", "-").replace("–", "-").split())
    return _WATERMARK.sub("", t, count=1).strip()


def meeting_date(text: str) -> date:
    t = normalize(text)[:400]
    for i, pat in enumerate(_DATE_PATTERNS):
        if m := pat.search(t):
            if i == 0:
                d, mon, y = m.groups()
            else:
                mon, d, y = m.groups()
            return date(int(y), MONTHS[mon.lower()], int(d))
    raise ValueError("statement: meeting date not found in header")


def _sign(verb: str) -> int:
    v = verb.lower()
    for stem, s in _ACTION_SIGN.items():
        if v.startswith(stem):
            return s
    raise ValueError(f"statement: unknown action verb {verb!r}")


@dataclass(frozen=True)
class Decision:
    change_bp: int
    new_rate: float


_DECISION = re.compile(
    r"(?:decided|agreed) to (?P<verb>raise|increase|lower|reduce|cut|keep|hold|maintain)"
    r" the monetary policy (?:interest )?rate(?: \(MPR\))?"
    r"(?: by (?P<bp>\d+) basis points)?,?(?: to| at| in) (?P<rate>\d+(?:[.,]\d+)?)\s?%",
    re.I,
)


def decision(text: str) -> Decision:
    m = _DECISION.search(normalize(text))
    if not m:
        raise ValueError("statement: decision sentence not found")
    sign = _sign(m["verb"])
    bp = int(m["bp"]) if m["bp"] else 0
    if (sign == 0) != (bp == 0):
        raise ValueError(f"statement: inconsistent decision ({m['verb']} by {bp}bp)")
    return Decision(sign * bp, float(m["rate"].replace(",", ".")))


@dataclass(frozen=True)
class MemberVote:
    name: str  # surname key, e.g. "Griffith-Jones"
    role: str  # Governor | Vice-Governor | Board member
    preferred_bp: int
    dissent: bool


@dataclass
class Vote:
    unanimous: bool
    votes: list[MemberVote] = field(default_factory=list)  # empty when unanimous (no names)
    raw: str = ""

    @property
    def dissents(self) -> list[MemberVote]:
        return [v for v in self.votes if v.dissent]


def _split_people(chunk: str) -> list[tuple[str, str]]:
    """'Governor Costa, Vice-Governor X, and Board members Naudon and Soto' -> [(role, name)]."""
    out: list[tuple[str, str]] = []
    # split before each role, but not at the "Governor" inside "Vice-Governor"
    parts = re.split(rf"(?<![-\w])(?={_ROLE}\b)", chunk)
    for part in parts:
        part = re.sub(r"[\s,]+and$", "", part.strip(" ,."))
        if not part:
            continue
        rm = re.match(rf"({_ROLE})\s+(.*)", part)
        if not rm:
            raise ValueError(f"statement: cannot read voters in {chunk!r}")
        role = "Vice-Governor" if rm[1].lower().startswith("vice") else (
            "Governor" if rm[1].lower() == "governor" else "Board member")  # fmt: skip
        names = re.split(r",\s*(?:and\s+)?|\s+and\s+", rm[2].strip(" ,"))
        out += [(role, n.strip().split()[-1]) for n in names if n.strip()]
    return out


_UNANIMOUS = re.compile(
    r"adopted (?:by|with) (?:the )?unanimous vote|adopted by all (?:the )?Board members", re.I
)
_MAJORITY = re.compile(r"adopted with the (?:favorable )?votes of (?P<who>[^.]+)\.", re.I)
_DISSENT = re.compile(
    rf"(?P<who>{_ROLE}\s[^.;]+?)\s+voted (?:for|to) (?P<verb>\w+)"
    r"(?: it| the MPR| the monetary policy (?:interest )?rate)?(?: by (?P<bp>\d+) basis points)?",
    re.I,
)


def vote(text: str, decided_bp: int) -> Vote:
    t = normalize(text)
    if m := _UNANIMOUS.search(t):
        return Vote(True, [], t[m.start() : m.start() + 120])
    m = _MAJORITY.search(t)
    if not m:
        raise ValueError("statement: vote sentence not found")
    votes = [MemberVote(n, r, decided_bp, False) for r, n in _split_people(m["who"])]
    tail = t[m.end() : m.end() + 700]
    for d in _DISSENT.finditer(tail):
        who = re.sub(r"^and\s+", "", d["who"].strip())
        pref = _sign(d["verb"]) * (int(d["bp"]) if d["bp"] else 0)
        votes += [MemberVote(n, r, pref, True) for r, n in _split_people(who)]
    if not any(v.dissent for v in votes):
        raise ValueError("statement: majority vote without a parsed dissent")
    return Vote(False, votes, t[m.start() : m.end() + 300])


@dataclass
class ParsedStatement:
    meeting_date: date
    meeting_id: str
    decision: Decision
    vote: Vote


def parse_statement(text: str) -> ParsedStatement:
    d = meeting_date(text)
    dec = decision(text)
    return ParsedStatement(d, f"RPM-{d:%Y-%m}", dec, vote(text, dec.change_bp))


def standard_filename(meeting: date, lang: str = "en") -> str:
    return f"rpm_{meeting.isoformat()}_statement_{lang}.pdf"
