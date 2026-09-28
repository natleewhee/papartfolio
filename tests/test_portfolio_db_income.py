"""income_events storage, run against a real local SQLite file (libsql's
Python API is sqlite3-compatible) — this is the table that makes IBKR
report re-reads idempotent, so the actual SQL is worth exercising."""
import sqlite3

import pytest

import portfolio_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setattr(portfolio_db, "_connect", lambda: sqlite3.connect(path))
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE income_events (id TEXT PRIMARY KEY, date DATE NOT NULL, symbol TEXT NOT NULL,
        kind TEXT NOT NULL, amount REAL NOT NULL, currency TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()


def _event(id, date="2026-09-12", kind="dividend", amount=8.3, currency="USD"):
    return {"id": id, "date": date, "symbol": "MSFT", "kind": kind, "amount": amount, "currency": currency}


def test_save_is_idempotent_and_returns_only_new(db):
    assert portfolio_db.save_income_events([_event("cash:1"), _event("cash:2")]) == [_event("cash:1"), _event("cash:2")]
    assert portfolio_db.save_income_events([_event("cash:2"), _event("cash:3")]) == [_event("cash:3")]
    assert portfolio_db.save_income_events([]) == []


def test_totals_since_group_by_kind_and_currency(db):
    portfolio_db.save_income_events([
        _event("cash:1", amount=8.0),
        _event("cash:2", kind="tax", amount=-2.0),
        _event("trade:3", kind="realized", amount=100.0, currency="SGD"),
        _event("cash:4", date="2025-12-31", amount=999.0),  # before the window
    ])
    assert portfolio_db.get_income_totals_since("2026-01-01") == {
        "dividend": {"USD": 8.0}, "tax": {"USD": -2.0}, "realized": {"SGD": 100.0},
    }
