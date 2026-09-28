from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from tests.services.fakes import FakeSource


@dataclass
class Harness:
    app: FastAPI
    client: TestClient
    source: FakeSource


@pytest.fixture(autouse=True)
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "finally.db"
    monkeypatch.setenv("DB_PATH", str(path))
    return path


@pytest.fixture
def make_harness(tmp_path):
    """Start an app (lifespan included) with a fake market source; stopped at teardown."""
    clients = []

    def _make(snapshot_interval: float = 3600, static_dir=None, preload_llm=False) -> Harness:
        sources = []

        def factory(cache):
            sources.append(FakeSource(cache))
            return sources[-1]

        app = create_app(
            source_factory=factory,
            snapshot_interval=snapshot_interval,
            static_dir=static_dir or tmp_path / "no-static",
            # Off by default so a background import can't race tests that stub app.llm
            preload_llm=preload_llm,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return Harness(app=app, client=client, source=sources[0])

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def harness(make_harness) -> Harness:
    return make_harness()


@pytest.fixture
def client(harness) -> TestClient:
    return harness.client
