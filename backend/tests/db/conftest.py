import pytest

from app import db


@pytest.fixture(autouse=True)
def db_path(tmp_path):
    """A fresh, initialized database for each test."""
    path = tmp_path / "finally.db"
    db.init_db(path)
    return path
