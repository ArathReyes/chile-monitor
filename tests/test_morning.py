"""Morning job, email (AgentMail) and release (GitHub asset) helpers — offline."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from monitor import notify, release, storage
from monitor.jobs import morning_all as m


def test_classify():
    assert m.classify("error: boom")[0] == "fail"
    assert m.classify({"a": 3, "b": 0})[:2] == ("ok", "2 items, 3 rows changed")
    st, detail, errs = m.classify({"a": 1, "b": "error: 500"})
    assert st == "warn" and errs == ["b: error: 500"] and "1 errors" in detail
    assert m.classify({"a": "error: x"})[0] == "fail"
    assert m.classify(12)[0] == "ok"


@pytest.mark.parametrize(
    "now,ok",
    [
        (datetime(2026, 1, 13, 10, 5, tzinfo=UTC), True),  # EST: 05:05 NY
        (datetime(2026, 1, 13, 9, 5, tzinfo=UTC), False),  # EST: 04:05 NY
        (datetime(2026, 7, 14, 9, 5, tzinfo=UTC), True),  # EDT: 05:05 NY
        (datetime(2026, 7, 14, 10, 5, tzinfo=UTC), False),  # EDT: 06:05 NY
    ],
)
def test_should_run_ny_hour_across_dst(now, ok):
    assert (m.should_run(now, 5, None) is None) is ok


def test_should_run_once_per_ny_day():
    now = datetime(2026, 1, 13, 10, 40, tzinfo=UTC)
    assert m.should_run(now, None, datetime(2026, 1, 13, 10, 6)) is not None
    assert m.should_run(now, None, datetime(2026, 1, 12, 10, 6)) is None
    # 03:00 UTC on the 13th is still the 12th in New York
    assert m.should_run(now, None, datetime(2026, 1, 13, 3, 0)) is None


def test_steps_monday_adds_master():
    names = [n for n, _ in m.steps(date(2026, 11, 16))]
    assert "Bond master + outstanding" in names
    assert "Bond master + outstanding" not in [n for n, _ in m.steps(date(2026, 11, 17))]


def test_run_steps_continues_after_failure():
    def boom(_):
        raise ValueError("layout changed")

    res = m.run_steps(None, [("a", boom), ("b", lambda c: {"x": 2})])
    assert [r.status for r in res] == ["fail", "ok"]
    assert "layout changed" in res[0].errors[0]
    assert m.overall(res) == "FAILED"


def test_report_subject_and_body():
    res = [m.StepResult("BDE", "ok", "3 items", 1.0), m.StepResult("SOMA", "warn", "x", 2, ["e1"])]
    subj, text, html = m.report(
        res, [("TPM", "2026-11-13")], "OK", datetime(2026, 11, 16, 10, 5, tzinfo=UTC), "tail"
    )
    assert subj.endswith("WARNINGS (1 of 2 steps)")
    assert "[WARN] SOMA" in text and "- e1" in text and "TPM: 2026-11-13" in text
    assert "<table" in html and "e1" in html


def test_email_payload_and_send(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "k")
    monkeypatch.setenv("AGENTMAIL_INBOX", "box@agentmail.to")
    monkeypatch.setenv("REPORT_TO", "a@example.com, b@example.com")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message_id": "m1", "thread_id": "t1"})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    att = notify.Attachment("run.log", b"hello")
    assert notify.send_email("S", "T", "<b>T</b>", [att], client=c) == "m1"
    assert seen["url"].endswith("/v0/inboxes/box%40agentmail.to/messages/send")
    assert seen["auth"] == "Bearer k"
    b = seen["body"]
    assert b["to"] == ["a@example.com", "b@example.com"] and b["html"] == "<b>T</b>"
    assert base64.b64decode(b["attachments"][0]["content"]) == b"hello"


def test_email_not_configured(monkeypatch):
    for k in ("AGENTMAIL_API_KEY", "AGENTMAIL_INBOX", "REPORT_TO"):
        monkeypatch.setenv(k, "")
    with pytest.raises(RuntimeError, match="not configured"):
        notify.send_email("s", "t")


def test_oversize_attachment_dropped():
    big = notify.Attachment("big.log", b"x" * 6_000_000)
    body = notify.build_message(["a@b"], "s", "t", None, [big])
    assert "attachments" not in body


def test_release_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATA_REPO", "me/repo")
    db = tmp_path / "src.duckdb"
    con = storage.connect(db)
    con.execute("CREATE TABLE t AS SELECT 42 AS x")
    con.close()
    gz = tmp_path / "db.gz"
    release.gzip_file(db, gz)
    blob = gz.read_bytes()
    asset = {"id": 7, "name": release.ASSET, "updated_at": "2026-11-16T10:20:00Z", "size": 9}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/releases/tags/data"):
            return httpx.Response(200, json={"id": 1, "assets": [asset]})
        if req.url.path.endswith("/releases/assets/7"):
            return httpx.Response(200, content=blob)
        return httpx.Response(404)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    dst = tmp_path / "out" / "monitor.duckdb"
    info = release.pull(dst, c)
    assert info.asset_id == 7 and info.updated_at.tzinfo is not None
    con = storage.connect(dst, read_only=True)
    assert con.execute("SELECT x FROM t").fetchall() == [(42,)]
    con.close()


def test_release_absent(monkeypatch):
    monkeypatch.setenv("DATA_REPO", "me/repo")
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert release.pull(Path("/nonexistent/x.duckdb"), c) is None
