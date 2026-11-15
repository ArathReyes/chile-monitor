"""BCCh documents (statements, minutes, IPoM, board, calendar) from manually saved files.

www.bcentral.cl HTML pages sit behind bot protection (Imperva Incapsula) and robots.txt disallows
``/*reunion-de-politica-monetaria-rpm*``, so pages are never fetched automatically. Instead the
user saves listing pages or PDFs into ``data/manual/bcch_web/<kind>/`` (see docs/manual-data.md).
This module:
  * parses saved HTML pages for document links (``/documents/...`` files),
  * downloads only links that robots.txt allows, rate-limited, never re-fetching a known URL,
  * extracts PDF text (pdfplumber), strips repeated headers/footers and page numbers,
  * stores originals under data/raw/bcch_web/<kind>/ and a ``documents`` row with sha256.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import duckdb
import pandas as pd
from bs4 import BeautifulSoup

from monitor import storage
from monitor.config import DATA_DIR
from monitor.http import HttpClient
from monitor.parsing import parse_cl_date
from monitor.sources.base import HealthResult, Source

log = logging.getLogger(__name__)

BASE_URL = "https://www.bcentral.cl"
MANUAL_DIR = DATA_DIR / "manual" / "bcch_web"
KINDS = ("statements", "minutes", "ipom", "board", "calendar", "surveys")
DOC_EXT = (".pdf", ".xlsx", ".xls", ".csv")
# Verified 2026-11-14 at https://www.bcentral.cl/robots.txt (User-Agent: *).
ROBOTS_DISALLOW = (
    "/*nuevo-buscador*",
    "/*new-search*",
    "/*reunion-de-politica-monetaria-rpm*",
    "/*buscador*",
    "/*search*",
)
MIN_TEXT_CHARS = 200  # below this, treat a PDF as scanned (no text layer)


# --------------------------------------------------------------------- helpers


def robots_allowed(url: str, rules: tuple[str, ...] = ROBOTS_DISALLOW) -> bool:
    """Wildcard-aware check of a URL path against Disallow rules."""
    path = urlsplit(url).path or "/"
    for rule in rules:
        pattern = "^" + re.escape(rule).replace(r"\*", ".*")
        if re.match(pattern, path):
            return False
    return True


@dataclass(frozen=True)
class DocLink:
    url: str
    text: str


def extract_document_links(html: str, base_url: str = BASE_URL) -> list[DocLink]:
    """Document links (PDF/Excel/CSV) on bcentral.cl found in a saved page, de-duplicated."""
    soup = BeautifulSoup(html, "lxml")
    host = urlsplit(base_url).netloc
    seen: dict[str, DocLink] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(base_url + "/", a["href"].strip())
        parts = urlsplit(url)
        if parts.netloc != host:
            continue
        path = unquote(parts.path).lower()
        if "/documents/" not in path and not path.endswith(DOC_EXT):
            continue
        clean = parts._replace(fragment="").geturl()
        text = " ".join(a.get_text(" ").split())
        if clean not in seen or (text and not seen[clean].text):
            seen[clean] = DocLink(clean, text)
    return list(seen.values())


_PAGE_NUM = re.compile(r"^\s*(p[áa]g(ina)?\.?\s*)?\d{1,3}(\s*(de|/)\s*\d{1,3})?\s*$", re.I)


def clean_pdf_pages(pages: list[str]) -> str:
    """Join page texts: drop repeated headers/footers and page numbers, rebuild paragraphs."""
    lines_per_page = [[ln.strip() for ln in (p or "").splitlines()] for p in pages]
    counts = Counter(ln for lines in lines_per_page for ln in set(lines) if ln)
    threshold = max(2, int(0.5 * len(pages)) + (1 if len(pages) > 2 else 0))
    repeated = {ln for ln, c in counts.items() if c >= threshold and len(pages) >= 3}
    paragraphs: list[str] = []
    current: list[str] = []
    for lines in lines_per_page:
        for ln in lines:
            if not ln:
                if current:
                    paragraphs.append(" ".join(current))
                    current = []
                continue
            if ln in repeated or _PAGE_NUM.match(ln):
                continue
            if current and current[-1].endswith("-") and not current[-1].endswith(" -"):
                current[-1] = current[-1][:-1] + ln  # re-join hyphenated word
            elif current and re.search(r"[.:;?!»”\"]$", current[-1]) and ln[:1].isupper():
                paragraphs.append(" ".join(current))
                current = [ln]
            else:
                current.append(ln)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(p for p in paragraphs if p)


def pdf_text(content: bytes) -> str | None:
    """Extracted text, or None if the PDF has no usable text layer (scanned)."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    text = clean_pdf_pages(pages)
    return text if len(text) >= MIN_TEXT_CHARS else None


_DATE_WORDS = r"\d{1,2}[.\-/ ](?:de )?[A-Za-záéíóú]{3,10}\.?[.\-/ ](?:de )?\d{4}"
_DATE_NUM = r"\d{1,2}[./-]\d{1,2}[./-]\d{4}"
_DATE_IN_TEXT = re.compile(f"({_DATE_WORDS}|{_DATE_NUM})")


def guess_date(*texts: str) -> date | None:
    for t in texts:
        for m in _DATE_IN_TEXT.finditer(t or ""):
            raw = re.sub(r"\s+de\s+", " ", m.group(1)).strip()
            try:
                return parse_cl_date(raw)
            except ValueError:
                continue
    return None


def _filename(url: str) -> str:
    name = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or "document"
    name = re.sub(r"[^\w.\-]+", "_", name)[:120]
    return name if name.lower().endswith(DOC_EXT) else name + ".pdf"


# ------------------------------------------------------------------ ingestion


def _store(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    content: bytes,
    filename: str,
    url: str | None,
    title: str,
) -> str:
    sha = hashlib.sha256(content).hexdigest()
    raw_dir = DATA_DIR / "raw" / "bcch_web" / kind
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{sha[:12]}_{filename}"
    path.write_bytes(content)
    text = pdf_text(content) if filename.lower().endswith(".pdf") else None
    if kind == "surveys" and filename.lower().endswith(".pdf"):
        _ingest_survey_pdf(conn, content, sha, filename)
    if filename.lower().endswith(".pdf") and text is None:
        log.warning("bcch_web: %s has no text layer (scanned?); stored without text", filename)
    storage.upsert_document(
        conn,
        doc_id=f"{kind}:{sha[:16]}",
        kind=kind,
        institution="BCCh",
        meeting_id=None,
        published_at=guess_date(title, filename),
        title=title or filename,
        url=url,
        raw_path=str(path),
        text=text,
        sha256=sha,
    )
    return sha


def _ingest_survey_pdf(
    conn: duckdb.DuckDBPyConnection, content: bytes, sha: str, filename: str
) -> None:
    """EEE results PDFs: parse the summary table into survey_distribution."""
    from monitor.sources import eee_pdf

    try:
        storage.upsert_survey_distribution(conn, eee_pdf.parse_pdf(content), sha)
    except ValueError as e:
        log.warning("bcch_web: %s is not a parseable EEE results PDF: %s", filename, e)


def ingest_kind(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    download: bool = True,
    manual_dir: Path = MANUAL_DIR,
) -> dict[str, int]:
    """Process one manual folder. Returns counts."""
    folder = manual_dir / kind
    stats = {"local_files": 0, "links_found": 0, "downloaded": 0, "skipped_known": 0,
             "blocked_robots": 0, "failed": 0}  # fmt: skip
    if not folder.exists():
        return stats
    known_urls = storage.document_urls(conn)
    known_sha = storage.document_shas(conn)
    links: list[DocLink] = []
    for f in sorted(folder.iterdir()):
        suffix = f.suffix.lower()
        if suffix in (".html", ".htm"):
            links += extract_document_links(f.read_text(encoding="utf-8", errors="replace"))
        elif suffix in DOC_EXT:
            content = f.read_bytes()
            stats["local_files"] += 1
            if hashlib.sha256(content).hexdigest() not in known_sha:
                _store(conn, kind, content, f.name, None, f.stem)
    stats["links_found"] = len(links)
    if not download:
        return stats
    with HttpClient() as client:
        for link in links:
            if link.url in known_urls:
                stats["skipped_known"] += 1
                continue
            if not robots_allowed(link.url):
                stats["blocked_robots"] += 1
                continue
            try:
                resp = client.get(link.url)
                ctype = resp.headers.get("content-type", "")
                if "html" in ctype:  # bot-challenge page or an HTML document, not a file
                    raise ValueError(f"got HTML instead of a document ({ctype})")
                _store(conn, kind, resp.content, _filename(link.url), link.url, link.text)
                known_urls.add(link.url)
                stats["downloaded"] += 1
            except Exception as e:  # noqa: BLE001 - one bad link must not stop the batch
                stats["failed"] += 1
                log.warning("bcch_web: %s failed: %s", link.url, e)
    return stats


def ingest_all(
    conn: duckdb.DuckDBPyConnection, download: bool = True
) -> dict[str, dict[str, int] | str]:
    out: dict[str, dict[str, int] | str] = {}
    for kind in KINDS:
        try:
            with storage.ingest_run(conn, f"bcch_web.{kind}", "bcch_web") as run:
                stats = ingest_kind(conn, kind, download)
                run.rows = stats["downloaded"] + stats["local_files"]
            out[kind] = stats
        except Exception as e:  # noqa: BLE001
            out[kind] = f"error: {e}"
    return out


class BcchWebSource(Source):
    name = "bcch_web"

    def fetch(self, *args, **kwargs) -> pd.DataFrame:
        raise NotImplementedError("use ingest_all(); documents come from manual files")

    def health_check(self) -> HealthResult:
        n = (
            sum(1 for k in KINDS for f in (MANUAL_DIR / k).glob("*") if f.is_file())
            if MANUAL_DIR.exists()
            else 0
        )
        if n == 0:
            return HealthResult(
                False, f"no manual files yet in {MANUAL_DIR} (see docs/manual-data.md)", "stub"
            )
        return HealthResult(True, f"{n} manual files in data/manual/bcch_web/", "ok")
