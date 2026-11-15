"""BCCh Board data from manually saved files (bcentral.cl blocks automated access).

* ``Histórico Autoridades.pdf`` (official list of authorities since 1926): sections PRESIDENTES,
  VICEPRESIDENTES, CONSEJEROS, GERENTES GENERALES with "start - end Name" rows; open-ended rows
  ("2017 - Rosanna Costa Costa") are current. Layout confirmed on the copy saved 2026-11-14
  (fixture: tests/fixtures/bcch_authorities_history.txt).
* Member profile pages (``.../the-bank/corporative-goverment/people/<slug>``, saved as HTML):
  name in ``.info-investigador p.f-opensans-bold``, title in the next paragraph, then the bio
  (fixture: tests/fixtures/bcch_person_page_cowan.html).
* Photos: the site's bot protection blocks image downloads, so the browser save stores a challenge
  page instead of the image. Real photos go in ``board/photos/<surname>.jpg|png``; anything that
  isn't a real image (checked by magic bytes) is ignored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from monitor.config import DATA_DIR

BOARD_DIR = DATA_DIR / "manual" / "bcch_web" / "board"
SECTIONS = {
    "PRESIDENTES": "Governor",
    "VICEPRESIDENTES": "Vice-Governor",
    "CONSEJEROS": "Board member",
    "GERENTES GENERALES": "General Manager",
}
_ROW = re.compile(r"^(\d{4})\s*-\s*(\d{4})?\s*(.+?)\s*(?:\((\d+)\))?\s*$")
_PARTICLES = {"de", "del", "de la", "la", "van", "von", "dell"}
_EDU = re.compile(
    r"\b(degree|bachelor\S*|Ph\.?\s?D|doctorate|master(?:['’]s)?\s+(?:degree|of|in)\b|M\.S\.|MBA|M\.?Sc|"
    r"engineer(?:ing)? from|licenciad\w*)",
    re.I,
)
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"GIF8")


def ascii_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", "-", s.lower()).strip("-")


def surname_key(full_name: str) -> str:
    """Paternal surname as used in statements: 'Rosanna Costa Costa' -> 'Costa',
    'Luis Felipe Céspedes Cifuentes' -> 'Céspedes', 'Kevin Cowan' -> 'Cowan'."""
    toks = full_name.replace("’", "'").split()
    if len(toks) <= 2:
        return toks[-1]
    key = toks[-2]
    if len(toks) >= 4 and toks[-3].lower() in _PARTICLES:
        key = f"{toks[-3]} {key}"
    return key


def parse_authorities(text: str) -> pd.DataFrame:
    """All rows of the authorities list. Raises if no section/rows are found."""
    rows, role = [], None
    for raw in text.splitlines():
        line = raw.strip().replace("’", "'")
        if line.upper() in SECTIONS:
            role = SECTIONS[line.upper()]
            continue
        if role and (m := _ROW.match(line)):
            name = m[3].strip()
            rows.append(
                {
                    "role": role,
                    "full_name": name,
                    "surname_key": surname_key(name),
                    "start_year": int(m[1]),
                    "end_year": int(m[2]) if m[2] else None,
                    "footnote": m[4],
                }
            )
    if not rows or {r["role"] for r in rows} != set(SECTIONS.values()):
        raise ValueError("authorities PDF: sections not found (layout changed?)")
    return pd.DataFrame(rows)


_ABBREV = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Sra|St|Jr)\.$")


def _sentences(text: str) -> list[str]:
    """Split on sentence ends, without breaking after titles like 'Mr.'."""
    out: list[str] = []
    for piece in re.split(r"(?<=[.;])\s+(?=[A-Z])", text):
        if out and _ABBREV.search(out[-1]):
            out[-1] = f"{out[-1]} {piece}"
        else:
            out.append(piece)
    return out


@dataclass
class Profile:
    name: str
    title: str
    bio: str
    education: list[str] = field(default_factory=list)
    photo_src: str | None = None
    source_file: str = ""


def parse_person_page(html: str, source_file: str = "") -> Profile:
    soup = BeautifulSoup(html, "lxml")
    info = soup.select_one(".info-investigador")
    if info is None:
        raise ValueError("person page: .info-investigador block not found (layout changed?)")
    paras = [" ".join(p.get_text(" ").split()) for p in info.find_all("p")]
    paras = [p for p in paras if p]
    if len(paras) < 3:
        raise ValueError("person page: expected name, title and bio paragraphs")
    name, title, bio = paras[0], paras[1], " ".join(paras[2:])
    sentences = _sentences(bio)
    education = [s.strip() for s in sentences if _EDU.search(s)]
    img = soup.select_one("img.description-item-image")
    return Profile(name, title, bio, education, img.get("src") if img else None, source_file)


def is_image(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return head.startswith(IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")


def find_photo(surname: str, board_dir: Path = BOARD_DIR) -> Path | None:
    """board/photos/<key>.(jpg|jpeg|png|webp) if it's a real image."""
    folder = board_dir / "photos"
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = folder / f"{ascii_key(surname)}.{ext}"
        if p.exists() and is_image(p):
            return p
    return None


def match_profile(full_name: str, profiles: list[Profile]) -> Profile | None:
    """Profile whose name tokens all appear in the authorities full name (accent-insensitive).

    Profile pages drop the maternal surname ('Luis Felipe Céspedes' vs
    'Luis Felipe Céspedes Cifuentes'), so surname position can't be used.
    """
    full = set(ascii_key(full_name).split("-"))
    hits = [p for p in profiles if set(ascii_key(p.name).split("-")) <= full]
    return max(hits, key=lambda p: len(p.name)) if hits else None


def load_profiles(board_dir: Path = BOARD_DIR) -> list[Profile]:
    out = []
    for f in sorted(board_dir.glob("*.htm*")):
        try:
            out.append(parse_person_page(f.read_text(encoding="utf-8", errors="replace"), f.name))
        except ValueError:
            continue  # not a profile page (e.g. the people listing)
    return out


def load_authorities(board_dir: Path = BOARD_DIR) -> pd.DataFrame:
    import pdfplumber

    pdfs = [p for p in board_dir.glob("*.pdf") if "autoridad" in p.name.lower()]
    if not pdfs:
        return pd.DataFrame()
    with pdfplumber.open(pdfs[0]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_authorities(text)
