"""Smoke test: every tab renders without exceptions (no network; empty DuckDB)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor.storage.DEFAULT_DB_PATH", tmp_path / "test.duckdb")
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    return at


def test_all_tabs_render(app):
    assert not app.exception, app.exception
    labels = [t.label for t in app.tabs]
    for name in [
        "Monetary Policy", "Chile Macro", "Curves & Rates", "FX & Flows", "Global Macro",
        "Calendar", "Bond Monitor", "Copper", "Thematic", "Data Health",
        "Overview", "Board & Communication", "Decision History",
        "Implied Path & Probabilities", "Surveys",
    ]:  # fmt: skip
        assert name in labels, name
