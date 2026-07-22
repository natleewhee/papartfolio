import pytest
import portfolio
from portfolio import fmt_money, format_holdings_table, get_currency_breakdown, calculate_portfolio_metrics


# ---------- fmt_money ----------

def test_fmt_money_basic():
    assert fmt_money(1234.5, "USD") == "$1,234.50"
    assert fmt_money(1234.5, "SGD") == "S$1,234.50"


def test_fmt_money_unknown_currency_falls_back_to_code_prefix():
    assert fmt_money(10, "EUR") == "EUR 10.00"


def test_fmt_money_privacy_masks_value():
    assert fmt_money(1234.5, "USD", privacy=True) == "•••"


def test_fmt_money_show_sign():
    assert fmt_money(50, "USD", show_sign=True) == "+$50.00"
    assert fmt_money(-50, "USD", show_sign=True) == "-$50.00"


def test_fmt_money_decimals():
    assert fmt_money(1234.5, "USD", decimals=0) == "$1,234"
    assert fmt_money(1.5, "USD", decimals=4) == "$1.5000"


# ---------- format_holdings_table ----------

def test_format_holdings_table_contains_header_and_rows():
    holdings = [
        {"symbol": "AAPL", "currency": "USD", "current_price": 110.0, "cost_basis": 1000,
         "current_value": 1100, "unrealized_gain_pct": 10.0, "daily_change_%": 10.0, "daily_change_$": 100},
        {"symbol": "MSFT", "currency": "USD", "current_price": 190.0, "cost_basis": 1000,
         "current_value": 950, "unrealized_gain_pct": -5.0, "daily_change_%": -5.0, "daily_change_$": -50},
    ]
    table = format_holdings_table(holdings)
    lines = table.split("\n")
    assert lines[0].startswith("SYMBOL")
    assert "GAIN%" in lines[0]
    assert "AAPL" in table and "MSFT" in table
    assert "+10.0%" in table
    assert "-5.0%" in table
    assert "$110.00" in table  # per-share price
    assert "$1,000" in table  # original cost basis


def test_format_holdings_table_privacy_masks_values():
    holdings = [
        {"symbol": "AAPL", "currency": "USD", "current_price": 110.0, "cost_basis": 1000,
         "current_value": 1100, "unrealized_gain_pct": 10.0, "daily_change_%": 10.0, "daily_change_$": 100},
    ]
    table = format_holdings_table(holdings, privacy=True)
    assert "1,100" not in table
    assert "110.00" not in table
    assert "•••" in table


# ---------- get_currency_breakdown ----------

def test_get_currency_breakdown_splits_by_currency():
    metrics = {
        "home_currency": "SGD",
        "fx_rates": {"USD": 1.35},
        "holdings": [
            {"currency": "USD", "current_value": 1000},
            {"currency": "SGD", "current_value": 1000},
        ],
    }
    breakdown = get_currency_breakdown(metrics)
    # USD leg is FX-adjusted to SGD terms before the split, so it's worth more
    assert breakdown["USD"] > breakdown["SGD"]
    assert sum(breakdown.values()) == pytest.approx(100.0, abs=0.1)


def test_get_currency_breakdown_empty_when_no_value():
    assert get_currency_breakdown({"home_currency": "SGD", "fx_rates": {}, "holdings": []}) == {}


# ---------- calculate_portfolio_metrics (monkeypatched DB/network boundary) ----------

def test_calculate_portfolio_metrics_aggregates_correctly(monkeypatch):
    holdings = [
        {"symbol": "AAPL", "shares": 10, "avg_cost": 100.0, "currency": "USD"},
        {"symbol": "MSFT", "shares": 5, "avg_cost": 200.0, "currency": "USD"},
    ]
    prices = {
        "AAPL": {"symbol": "AAPL", "price": 110.0, "change": 10.0, "change_pct": 10.0},
        "MSFT": {"symbol": "MSFT", "price": 190.0, "change": -10.0, "change_pct": -5.0},
    }

    monkeypatch.setattr(portfolio, "get_all_holdings", lambda: holdings)
    monkeypatch.setattr(portfolio, "get_prices_bulk", lambda symbols: prices)
    monkeypatch.setattr(portfolio, "fetch_fx_rate", lambda frm, to: 1.35)
    monkeypatch.setattr(portfolio, "HOME_CURRENCY", "SGD")

    metrics = calculate_portfolio_metrics(save_snapshot=False)

    assert metrics["home_currency"] == "SGD"
    assert metrics["total_value"] == pytest.approx(2767.5, abs=0.01)
    assert metrics["total_cost_basis"] == pytest.approx(2700.0, abs=0.01)
    assert metrics["unrealized_gain"] == pytest.approx(67.5, abs=0.01)
    assert metrics["unrealized_gain_pct"] == pytest.approx(2.5, abs=0.01)
    assert metrics["daily_change_$"] == pytest.approx(67.5, abs=0.01)
    assert metrics["daily_change_%"] == pytest.approx(2.5, abs=0.01)
    assert metrics["best_performer"]["symbol"] == "AAPL"
    assert metrics["worst_performer"]["symbol"] == "MSFT"
    assert not metrics["failed_symbols"]

    pct_total = sum(h["pct_of_portfolio"] for h in metrics["holdings"])
    assert pct_total == pytest.approx(100.0, abs=0.1)


def test_calculate_portfolio_metrics_skips_failed_price_fetch(monkeypatch):
    holdings = [
        {"symbol": "AAPL", "shares": 10, "avg_cost": 100.0, "currency": "USD"},
        {"symbol": "BAD", "shares": 5, "avg_cost": 50.0, "currency": "USD"},
    ]
    prices = {
        "AAPL": {"symbol": "AAPL", "price": 110.0, "change": 10.0, "change_pct": 10.0},
        "BAD": None,
    }

    monkeypatch.setattr(portfolio, "get_all_holdings", lambda: holdings)
    monkeypatch.setattr(portfolio, "get_prices_bulk", lambda symbols: prices)
    monkeypatch.setattr(portfolio, "fetch_fx_rate", lambda frm, to: 1.0)
    monkeypatch.setattr(portfolio, "HOME_CURRENCY", "USD")

    metrics = calculate_portfolio_metrics(save_snapshot=False)

    assert metrics["failed_symbols"] == ["BAD"]
    assert len(metrics["holdings"]) == 1
    assert metrics["holdings"][0]["symbol"] == "AAPL"


def test_calculate_portfolio_metrics_sorts_by_position_size(monkeypatch):
    # MSFT is the bigger position but AAPL had the bigger move today — display
    # order should lead with position size, not today's mover.
    holdings = [
        {"symbol": "AAPL", "shares": 10, "avg_cost": 100.0, "currency": "USD"},
        {"symbol": "MSFT", "shares": 50, "avg_cost": 200.0, "currency": "USD"},
    ]
    prices = {
        "AAPL": {"symbol": "AAPL", "price": 150.0, "change": 50.0, "change_pct": 50.0},
        "MSFT": {"symbol": "MSFT", "price": 201.0, "change": 1.0, "change_pct": 0.5},
    }

    monkeypatch.setattr(portfolio, "get_all_holdings", lambda: holdings)
    monkeypatch.setattr(portfolio, "get_prices_bulk", lambda symbols: prices)
    monkeypatch.setattr(portfolio, "fetch_fx_rate", lambda frm, to: 1.0)
    monkeypatch.setattr(portfolio, "HOME_CURRENCY", "USD")

    metrics = calculate_portfolio_metrics(save_snapshot=False)

    assert [h["symbol"] for h in metrics["holdings"]] == ["MSFT", "AAPL"]
    assert metrics["best_performer"]["symbol"] == "AAPL"  # unaffected by display order


def test_calculate_portfolio_metrics_empty_portfolio(monkeypatch):
    monkeypatch.setattr(portfolio, "get_all_holdings", lambda: [])
    metrics = calculate_portfolio_metrics(save_snapshot=False)
    assert metrics["holdings"] == []
    assert metrics["total_value"] == 0
