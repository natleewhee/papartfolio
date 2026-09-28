import asyncio

import alerts


def test_alerts_for_closed_markets_are_skipped(monkeypatch):
    checked = []

    async def _fake_check(alert):
        checked.append(alert["symbol"])

    monkeypatch.setattr(alerts, "get_active_alerts", lambda: [
        {"id": 1, "symbol": "AAPL", "direction": "above", "threshold": 1},
        {"id": 2, "symbol": "D05.SI", "direction": "above", "threshold": 1},
    ])
    monkeypatch.setattr(alerts, "_check_threshold_alert", _fake_check)
    monkeypatch.setattr(alerts, "is_market_open", lambda key: key == "SG")

    asyncio.run(alerts.check_price_alerts())
    assert checked == ["D05.SI"]


def test_unknown_market_is_always_checked(monkeypatch):
    monkeypatch.setattr(alerts, "get_currency_for_symbol", lambda s: "EUR")
    monkeypatch.setattr(alerts, "is_market_open", lambda key: False)
    assert alerts._market_open_for("SAP.DE")
