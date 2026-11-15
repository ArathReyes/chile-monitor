# Manual data: BCCh documents

**Why this is manual:** www.bcentral.cl web pages are behind bot protection (Imperva Incapsula). Automated requests get a challenge page instead of content, and the app never tries to get around it. robots.txt also disallows `/*reunion-de-politica-monetaria-rpm*`. Document files under `/documents/...` (PDF, Excel) download normally and robots.txt allows them.

So the workflow is: **you save the listing pages (or the files) from your browser, and the app does the rest.** The app extracts the document links from saved pages, downloads the PDFs (at most 1 per second, only URLs robots.txt allows, never re-fetching a known URL), extracts the text and stores everything in DuckDB.

## Where to save

Root folder: `chile-monitor/data/manual/bcch_web/` (inside `data/`, which is gitignored).

| Folder | What to save | Where on bcentral.cl | Used for |
|---|---|---|---|
| `statements/` | RPM statement listing pages (one per year, all years), or the statement PDFs | Política Monetaria → Reunión de Política Monetaria (RPM) → Comunicados | Statement archive, diff, vote record, decision dates |
| `minutes/` | RPM minutes listing pages, or the minutes PDFs | Same RPM section → Actas / Minutas | Vote detail, dissent rationale, key passages |
| `ipom/` | IPoM listing page + the latest IPoM's page; any Excel data files ("Gráficos", "Base de datos") | Política Monetaria → Informe de Política Monetaria (IPoM) | TPM corridor, forecast table |
| `board/` | The Consejo page (and the former-members page if there is one) | Institucional / Quiénes somos → Consejo | Board cards, dissent counts |
| `calendar/` | The 2026 and 2027 monetary policy calendar page | Prensa → Calendario de política monetaria y financiera. Polymarket's market rules cite `https://www.bcentral.cl/es/web/banco-central/noticias-y-publicaciones/prensa/calendario-de-politica-monetaria-y-financiera`; not verified by the app because the site blocks it | Meeting dates (`config/meetings.yaml`) |
| `surveys/` | The monthly EEE results PDF, "Gráficos (distribución de respuestas)" | Estadísticas → Encuestas → Encuesta de Expectativas Económicas | EEE median, decile 1/9 and respondent counts, incl. "next / following meeting" TPM |

**How to save a page:** open it in the browser, press Cmd+S, and choose the format "Webpage, HTML Only" (or "Page Source"). Any `.html`/`.htm` file name works. You can save PDFs or Excel files directly into the same folders instead.

## Then run

```bash
uv run python -m monitor.jobs.mp_daily            # BDE + documents + markets
uv run python -m monitor.jobs.mp_daily --no-download   # parse saved files only
```

Re-running is safe: known URLs and files (by sha256) are skipped. Scanned PDFs (no text layer) are stored without text and logged; OCR isn't used.

## What's verified

- `https://www.bcentral.cl/robots.txt` (checked 2026-11-14): disallows `/*nuevo-buscador*`, `/*new-search*`, `/*reunion-de-politica-monetaria-rpm*`, `/*buscador*`, `/*search*`.
- A `/documents/` PDF (the October 2026 EEE results) downloaded normally (HTTP 200, `application/pdf`), and its text layer extracts cleanly.
