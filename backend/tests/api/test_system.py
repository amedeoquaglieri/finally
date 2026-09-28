import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from tests.services.fakes import FakeSource


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sse_route_registered(harness):
    paths = {route.path for route in harness.app.routes}
    assert "/api/stream/prices" in paths


def test_startup_initializes_db_and_starts_source(harness, db_path):
    assert db_path.exists()
    assert harness.source.started
    assert harness.source.get_tickers() == db.DEFAULT_TICKERS
    assert len(db.get_snapshots()) == 1  # initial snapshot


def test_startup_tracks_held_tickers_off_watchlist(make_harness, db_path):
    db.init_db(db_path)
    db.record_trade("PYPL", "buy", 5, 60.0)
    harness = make_harness()
    assert "PYPL" in harness.source.get_tickers()
    position = harness.client.get("/api/portfolio").json()["positions"][0]
    assert position["ticker"] == "PYPL"
    assert position["current_price"] == 60.0


def test_shutdown_stops_source(db_path, tmp_path):
    sources = []
    app = create_app(
        source_factory=lambda cache: sources.append(FakeSource(cache)) or sources[-1],
        static_dir=tmp_path / "none",
        preload_llm=False,
    )
    with TestClient(app):
        assert not sources[0].stopped
    assert sources[0].stopped


def _wait_for_preload(harness):
    task = harness.app.state.llm_preload
    deadline = time.monotonic() + 10
    while not task.done() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert task.done() and task.exception() is None


@pytest.fixture
def imported(monkeypatch):
    """Record (and skip) the preload's imports, so tests never load litellm."""
    names = []
    monkeypatch.setattr(
        "app.main.importlib",
        types.SimpleNamespace(import_module=lambda name: names.append(name)),
    )
    return names


@pytest.mark.parametrize(
    ("llm_mock", "expected"), [("true", ["app.llm"]), ("false", ["app.llm", "litellm"])]
)
def test_llm_stack_preloaded_in_background(make_harness, monkeypatch, imported, llm_mock, expected):
    monkeypatch.setenv("LLM_MOCK", llm_mock)
    harness = make_harness(preload_llm=True)
    _wait_for_preload(harness)
    assert imported == expected
    assert harness.client.get("/api/health").status_code == 200


def test_slow_preload_blocks_neither_startup_nor_shutdown(db_path, tmp_path, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(
        "app.main.importlib", types.SimpleNamespace(import_module=lambda name: release.wait(10))
    )
    app = create_app(source_factory=lambda cache: FakeSource(cache), static_dir=tmp_path / "none")
    started = time.monotonic()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not app.state.llm_preload.done()
    assert time.monotonic() - started < 5
    release.set()


def test_failed_preload_is_logged_not_raised(make_harness, monkeypatch, caplog):
    def fail(name):
        raise ImportError(f"no {name}")

    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setattr("app.main.importlib", types.SimpleNamespace(import_module=fail))
    harness = make_harness(preload_llm=True)
    _wait_for_preload(harness)
    assert "Failed to preload app.llm" in caplog.text
    assert harness.client.get("/api/health").status_code == 200


def test_llm_preload_disabled(harness):
    assert harness.app.state.llm_preload is None


def test_periodic_snapshots(make_harness):
    make_harness(snapshot_interval=0.05)
    deadline = time.monotonic() + 3
    while len(db.get_snapshots()) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(db.get_snapshots()) >= 3


def test_serves_static_frontend(make_harness, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>FinAlly</html>")
    client = make_harness(static_dir=static).client

    root = client.get("/")
    assert root.status_code == 200
    assert "FinAlly" in root.text
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/watchlist").status_code == 200


def test_no_static_dir_serves_api_only(client):
    assert client.get("/").status_code == 404
