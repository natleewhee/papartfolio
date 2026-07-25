import pytest
import ibkr_flex
from ibkr_flex import (
    is_configured, _parse_positions, _request_statement, _fetch_statement,
    reconcile_holdings, _format_summary,
)


# ---------- is_configured ----------

def test_is_configured_true_when_both_set(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    assert is_configured() is True


def test_is_configured_false_when_either_missing(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", None)
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    assert is_configured() is False


# ---------- _parse_positions ----------

def _flex_xml(*positions_xml):
    body = "".join(positions_xml)
    return f"""<FlexQueryResponse queryName="Test" type="AF">
  <FlexStatements count="1">
    <FlexStatement accountId="U123" fromDate="20260101" toDate="20260122" period="LastBusinessDay" whenGenerated="20260122;120000">
      <OpenPositions>
        {body}
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>"""


def test_parse_positions_basic_us_and_sg():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" '
        'position="15" costBasisPrice="148.10" />',
        '<OpenPosition accountId="U123" currency="SGD" assetCategory="STK" symbol="D05" '
        'position="100" costBasisPrice="30.5" />',
    )
    positions = _parse_positions(xml)

    assert len(positions) == 2
    aapl = next(p for p in positions if p["symbol"] == "AAPL")
    assert aapl == {"symbol": "AAPL", "shares": 15, "avg_cost": 148.1, "currency": "USD"}
    dbs = next(p for p in positions if p["symbol"] == "D05.SI")
    assert dbs["shares"] == 100 and dbs["currency"] == "SGD"


def test_parse_positions_skips_non_stock():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="OPT" symbol="AAPL 250101C00200000" '
        'position="1" costBasisPrice="5.0" />',
    )
    assert _parse_positions(xml) == []


def test_parse_positions_skips_missing_fields():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" position="15" />',  # no cost
    )
    assert _parse_positions(xml) == []


def test_parse_positions_skips_unsupported_currency():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="EUR" assetCategory="STK" symbol="SAP" '
        'position="10" costBasisPrice="120.0" />',
    )
    assert _parse_positions(xml) == []


def test_parse_positions_skips_zero_shares():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" '
        'position="0" costBasisPrice="148.10" />',
    )
    assert _parse_positions(xml) == []


def test_parse_positions_avg_cost_fallback_field_names():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="MSFT" '
        'position="5" avgCost="400.0" />',
    )
    positions = _parse_positions(xml)
    assert positions[0]["avg_cost"] == 400.0


def test_parse_positions_malformed_xml_returns_none():
    assert _parse_positions("<not><valid") is None


def test_parse_positions_sg_symbol_already_has_suffix_not_doubled():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="SGD" assetCategory="STK" symbol="D05.SI" '
        'position="100" costBasisPrice="30.5" />',
    )
    positions = _parse_positions(xml)
    assert positions[0]["symbol"] == "D05.SI"


# ---------- _request_statement / _fetch_statement ----------

class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_request_statement_success(monkeypatch):
    xml = '<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    assert _request_statement() == "REF123"


def test_request_statement_failure(monkeypatch):
    xml = '<FlexStatementResponse><Status>Fail</Status><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    assert _request_statement() is None


def test_fetch_statement_ready_immediately(monkeypatch):
    xml = _flex_xml()
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    assert _fetch_statement("REF") == xml


def test_fetch_statement_retries_until_ready(monkeypatch):
    monkeypatch.setattr(ibkr_flex.time, "sleep", lambda s: None)
    not_ready = '<FlexStatementResponse><Status>Warn</Status><ErrorMessage>not ready</ErrorMessage></FlexStatementResponse>'
    ready = _flex_xml()
    calls = {"n": 0}

    def fake_get(url, params, timeout):
        calls["n"] += 1
        return _FakeResponse(not_ready if calls["n"] < 3 else ready)

    monkeypatch.setattr(ibkr_flex.requests, "get", fake_get)
    assert _fetch_statement("REF") == ready
    assert calls["n"] == 3


def test_fetch_statement_never_ready_returns_none(monkeypatch):
    monkeypatch.setattr(ibkr_flex.time, "sleep", lambda s: None)
    not_ready = '<FlexStatementResponse><Status>Warn</Status><ErrorMessage>not ready</ErrorMessage></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(not_ready))
    assert _fetch_statement("REF") is None


# ---------- reconcile_holdings ----------

def test_reconcile_not_configured(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", None)
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", None)
    result = reconcile_holdings()
    assert result["status"] == "not_configured"


def test_reconcile_fetch_failed(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: None)
    result = reconcile_holdings()
    assert result["status"] == "fetch_failed"


def test_reconcile_refuses_to_wipe_on_empty_result(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: [])
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [
        {"symbol": "AAPL", "shares": 10, "avg_cost": 100.0, "currency": "USD"},
    ])
    remove_calls = []
    monkeypatch.setattr(ibkr_flex, "remove_holding", lambda s: remove_calls.append(s))

    result = reconcile_holdings()

    assert result["status"] == "skipped_empty"
    assert remove_calls == []  # never touched the DB


def test_reconcile_empty_ibkr_and_empty_db_is_a_harmless_ok(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: [])
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [])

    result = reconcile_holdings()

    assert result == {"status": "ok", "added": [], "updated": [], "removed": []}


def test_reconcile_adds_updates_and_removes(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: [
        {"symbol": "NVDA", "shares": 5, "avg_cost": 130.0, "currency": "USD"},   # new
        {"symbol": "AAPL", "shares": 15, "avg_cost": 148.1, "currency": "USD"},  # changed shares
        {"symbol": "MSFT", "shares": 3, "avg_cost": 400.0, "currency": "USD"},   # unchanged
    ])
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [
        {"symbol": "AAPL", "shares": 10, "avg_cost": 148.1, "currency": "USD"},
        {"symbol": "MSFT", "shares": 3, "avg_cost": 400.0, "currency": "USD"},
        {"symbol": "OLD", "shares": 7, "avg_cost": 50.0, "currency": "USD"},  # gone from IBKR
    ])
    monkeypatch.setattr(ibkr_flex, "add_holding", lambda *a, **k: True)
    monkeypatch.setattr(ibkr_flex, "update_holding", lambda *a, **k: True)
    monkeypatch.setattr(ibkr_flex, "remove_holding", lambda s: True)

    result = reconcile_holdings()

    assert result["status"] == "ok"
    assert [p["symbol"] for p in result["added"]] == ["NVDA"]
    assert [u["symbol"] for u in result["updated"]] == ["AAPL"]
    assert [h["symbol"] for h in result["removed"]] == ["OLD"]


def test_reconcile_no_changes_when_everything_matches(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    same = [{"symbol": "AAPL", "shares": 10, "avg_cost": 148.1, "currency": "USD"}]
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: same)
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: same)

    result = reconcile_holdings()

    assert result == {"status": "ok", "added": [], "updated": [], "removed": []}


# ---------- _format_summary ----------

def test_format_summary_not_configured_returns_none():
    assert _format_summary({"status": "not_configured", "added": [], "updated": [], "removed": []}) is None


def test_format_summary_fetch_failed():
    msg = _format_summary({"status": "fetch_failed", "added": [], "updated": [], "removed": []})
    assert "Couldn't fetch" in msg


def test_format_summary_skipped_empty():
    msg = _format_summary({"status": "skipped_empty", "added": [], "updated": [], "removed": []})
    assert "zero stock positions" in msg


def test_format_summary_no_changes():
    msg = _format_summary({"status": "ok", "added": [], "updated": [], "removed": []})
    assert "already match" in msg


def test_format_summary_with_changes():
    result = {
        "status": "ok",
        "added": [{"symbol": "NVDA", "shares": 5, "avg_cost": 130.0, "currency": "USD"}],
        "updated": [{"symbol": "AAPL", "from": {"shares": 10, "avg_cost": 148.1},
                     "to": {"shares": 15, "avg_cost": 148.1}}],
        "removed": [{"symbol": "OLD", "shares": 7, "avg_cost": 50.0, "currency": "USD"}],
    }
    msg = _format_summary(result)
    assert "Added NVDA: 5 @ 130.00 USD" in msg
    assert "Updated AAPL: 10→15 shares" in msg
    assert "Removed OLD" in msg
