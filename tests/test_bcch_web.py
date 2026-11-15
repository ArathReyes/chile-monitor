from datetime import date

import pytest

from monitor import storage
from monitor.sources.bcch_web import (
    clean_pdf_pages,
    extract_document_links,
    guess_date,
    ingest_kind,
    robots_allowed,
)

SAVED_PAGE = """
<html><body>
<a href="/documents/33528/123/comunicado-rpm-octubre-2026.pdf">
  Comunicado RPM 27 de octubre de 2026</a>
<a href="https://www.bcentral.cl/documents/33528/456/Minuta.pdf?version=1.0">Minuta</a>
<a href="/documents/33528/123/comunicado-rpm-octubre-2026.pdf#page=2"></a>
<a href="https://example.com/documents/x.pdf">other host</a>
<a href="/web/banco-central/politica-monetaria/reunion-de-politica-monetaria-rpm">RPM page</a>
</body></html>
"""


def test_extract_document_links():
    links = extract_document_links(SAVED_PAGE)
    urls = [lk.url for lk in links]
    assert urls == [
        "https://www.bcentral.cl/documents/33528/123/comunicado-rpm-octubre-2026.pdf",
        "https://www.bcentral.cl/documents/33528/456/Minuta.pdf?version=1.0",
    ]
    assert links[0].text.startswith("Comunicado RPM")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://www.bcentral.cl/documents/33528/1/x.pdf", True),
        ("https://www.bcentral.cl/web/x/reunion-de-politica-monetaria-rpm/2026", False),
        ("https://www.bcentral.cl/es/buscador?q=x", False),
    ],
)
def test_robots_allowed(url, ok):
    assert robots_allowed(url) is ok


def test_clean_pdf_pages_strips_headers_and_page_numbers():
    header = "BANCO CENTRAL DE CHILE"
    pages = [
        f"{header}\nEl Consejo acordó mantener la TPM\nen 4,5%.\n\nLa inflación\n1",
        f"{header}\nse ha moderado. El esce-\nnario externo sigue incierto.\n2",
        f"{header}\nTexto final del documento.\nPágina 3 de 3",
    ]
    text = clean_pdf_pages(pages)
    assert header not in text
    assert "acordó mantener la TPM en 4,5%." in text
    assert "escenario externo" in text
    assert "\n\n" in text and "Página 3" not in text


def test_guess_date():
    assert guess_date("Comunicado RPM 27 de octubre de 2026") == date(2026, 10, 27)
    assert guess_date("minuta-15-12-2026.pdf") == date(2026, 12, 15)
    assert guess_date("sin fecha") is None


def test_ingest_kind_local_pdf_and_saved_page(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor.sources.bcch_web.DATA_DIR", tmp_path)
    monkeypatch.setattr("monitor.sources.bcch_web.pdf_text", lambda b: "texto " * 50)
    folder = tmp_path / "manual" / "statements"
    folder.mkdir(parents=True)
    (folder / "comunicado-27-10-2026.pdf").write_bytes(b"%PDF-fake-1")
    (folder / "listado.html").write_text(SAVED_PAGE, encoding="utf-8")
    conn = storage.connect(":memory:")
    stats = ingest_kind(conn, "statements", download=False, manual_dir=tmp_path / "manual")
    assert stats["local_files"] == 1 and stats["links_found"] == 2
    docs = storage.read_documents(conn, "statements")
    assert len(docs) == 1 and docs["published_at"].iloc[0].date() == date(2026, 10, 27)
    ingest_kind(conn, "statements", download=False, manual_dir=tmp_path / "manual")
    assert len(storage.read_documents(conn)) == 1  # incremental: same sha not re-added


def test_eee_pdf_summary(load_text_fixture):
    from monitor.sources.eee_pdf import parse_summary, tpm_rows

    df = parse_summary(load_text_fixture("eee_pdf_page1_oct2026.txt"))
    assert df["survey_month"].iloc[0] == date(2026, 10, 1)
    tpm = tpm_rows(df).set_index("horizon")
    nxt = tpm.loc["En la siguiente reunión"]
    assert (nxt["median"], nxt["p10"], nxt["p90"], nxt["n"]) == (4.5, 4.5, 4.5, 49)
    assert tpm.loc["Dentro de 35 meses", "median"] == 4.25
    assert df["n"].min() > 0 and len(df) > 30


def test_eee_pdf_layout_change_raises():
    from monitor.sources.eee_pdf import parse_summary

    with pytest.raises(ValueError):
        parse_summary("ENCUESTA DE EXPECTATIVAS ECONÓMICAS Octubre 2026\nnothing here")
