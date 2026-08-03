import asyncio
import pytest
import ibkr_flex
from ibkr_flex import (
    is_configured, _parse_positions, _request_statement, _fetch_statement,
    fetch_flex_positions, reconcile_holdings, _format_summary, _compare_pricing,
    get_last_reconciled_at,
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
    assert aapl == {"symbol": "AAPL", "shares": 15, "avg_cost": 148.1, "currency": "USD",
                     "mark_price": None, "unrealized_pnl": None}
    dbs = next(p for p in positions if p["symbol"] == "D05.SI")
    assert dbs["shares"] == 100 and dbs["currency"] == "SGD"


def test_parse_positions_captures_optional_mark_price_and_pnl():
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" '
        'position="15" costBasisPrice="148.10" markPrice="150.00" fifoPnlUnrealized="28.50" />',
    )
    positions = _parse_positions(xml)
    assert positions[0]["mark_price"] == 150.0
    assert positions[0]["unrealized_pnl"] == 28.5


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


def test_parse_positions_keeps_fractional_shares():
    """A fractional position (DRIP, fractional-share brokers) used to be
    truncated to an int via int(float(position)), silently understating
    value/gain and re-flagging a spurious 'updated' diff on every future
    reconciliation."""
    xml = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" '
        'position="12.734" costBasisPrice="148.10" />',
    )
    positions = _parse_positions(xml)
    assert positions[0]["shares"] == pytest.approx(12.734)


# ---------- _request_statement / _fetch_statement ----------

class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_request_statement_success(monkeypatch):
    xml = '<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF123</ReferenceCode></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    assert _request_statement() == ("REF123", None)


def test_request_statement_failure_surfaces_error_message(monkeypatch):
    xml = '<FlexStatementResponse><Status>Fail</Status><ErrorCode>1003</ErrorCode><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    reference_code, error = _request_statement()
    assert reference_code is None
    assert "Invalid token" in error
    assert "1003" in error


def test_fetch_statement_ready_immediately(monkeypatch):
    xml = _flex_xml()
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))
    assert _fetch_statement("REF") == (xml, None)


def test_fetch_statement_retries_until_ready(monkeypatch):
    monkeypatch.setattr(ibkr_flex.time, "sleep", lambda s: None)
    not_ready = '<FlexStatementResponse><Status>Warn</Status><ErrorMessage>not ready</ErrorMessage></FlexStatementResponse>'
    ready = _flex_xml()
    calls = {"n": 0}

    def fake_get(url, params, timeout):
        calls["n"] += 1
        return _FakeResponse(not_ready if calls["n"] < 3 else ready)

    monkeypatch.setattr(ibkr_flex.requests, "get", fake_get)
    assert _fetch_statement("REF") == (ready, None)
    assert calls["n"] == 3


def test_fetch_statement_never_ready_returns_last_error(monkeypatch):
    monkeypatch.setattr(ibkr_flex.time, "sleep", lambda s: None)
    not_ready = '<FlexStatementResponse><Status>Warn</Status><ErrorMessage>Statement generation in progress</ErrorMessage></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(not_ready))
    xml_text, error = _fetch_statement("REF")
    assert xml_text is None
    assert "Statement generation in progress" in error


# ---------- fetch_flex_positions (end-to-end error propagation) ----------

def test_fetch_flex_positions_propagates_send_request_error(monkeypatch):
    xml = '<FlexStatementResponse><Status>Fail</Status><ErrorMessage>Invalid token</ErrorMessage></FlexStatementResponse>'
    monkeypatch.setattr(ibkr_flex.requests, "get", lambda url, params, timeout: _FakeResponse(xml))

    positions, error = fetch_flex_positions()

    assert positions is None
    assert "Invalid token" in error


def test_fetch_flex_positions_success_end_to_end(monkeypatch):
    send_ok = '<FlexStatementResponse><Status>Success</Status><ReferenceCode>REF</ReferenceCode></FlexStatementResponse>'
    report = _flex_xml(
        '<OpenPosition accountId="U123" currency="USD" assetCategory="STK" symbol="AAPL" '
        'position="15" costBasisPrice="148.10" />',
    )
    calls = {"n": 0}

    def fake_get(url, params, timeout):
        calls["n"] += 1
        return _FakeResponse(send_ok if calls["n"] == 1 else report)

    monkeypatch.setattr(ibkr_flex.requests, "get", fake_get)

    positions, error = fetch_flex_positions()

    assert error is None
    assert positions == [{"symbol": "AAPL", "shares": 15, "avg_cost": 148.1, "currency": "USD",
                          "mark_price": None, "unrealized_pnl": None}]


# ---------- reconcile_holdings ----------

def test_reconcile_not_configured(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", None)
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", None)
    result = reconcile_holdings()
    assert result["status"] == "not_configured"


def test_reconcile_fetch_failed_carries_error_reason(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: (None, "Invalid token (code 1003)"))
    result = reconcile_holdings()
    assert result["status"] == "fetch_failed"
    assert result["error"] == "Invalid token (code 1003)"


def test_reconcile_refuses_to_wipe_on_empty_result(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: ([], None))
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
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: ([], None))
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [])

    result = reconcile_holdings()

    assert result == {"status": "ok", "added": [], "updated": [], "removed": [], "pricing": None}


def test_reconcile_adds_updates_and_removes(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: ([
        {"symbol": "NVDA", "shares": 5, "avg_cost": 130.0, "currency": "USD"},   # new
        {"symbol": "AAPL", "shares": 15, "avg_cost": 148.1, "currency": "USD"},  # changed shares
        {"symbol": "MSFT", "shares": 3, "avg_cost": 400.0, "currency": "USD"},   # unchanged
    ], None))
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
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: (same, None))
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: same)

    result = reconcile_holdings()

    assert result == {"status": "ok", "added": [], "updated": [], "removed": [], "pricing": None}


def test_reconcile_tolerates_float_noise_on_fractional_shares(monkeypatch):
    """Shares are now a float (see test_parse_positions_keeps_fractional_shares)
    — an exact != comparison would flap on harmless float storage/round-trip
    noise the same way avg_cost's own tolerance already guards against."""
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: (
        [{"symbol": "AAPL", "shares": 12.734, "avg_cost": 148.1, "currency": "USD"}], None,
    ))
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [
        {"symbol": "AAPL", "shares": 12.73400001, "avg_cost": 148.1, "currency": "USD"},
    ])
    update_calls = []
    monkeypatch.setattr(ibkr_flex, "update_holding", lambda *a, **k: update_calls.append(a))

    result = reconcile_holdings()

    assert result["updated"] == []
    assert update_calls == []


def test_reconcile_updates_on_real_fractional_share_change(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_TOKEN", "tok")
    monkeypatch.setattr(ibkr_flex, "IBKR_FLEX_QUERY_ID", "123")
    monkeypatch.setattr(ibkr_flex, "fetch_flex_positions", lambda: (
        [{"symbol": "AAPL", "shares": 12.734, "avg_cost": 148.1, "currency": "USD"}], None,
    ))
    monkeypatch.setattr(ibkr_flex, "get_all_holdings", lambda: [
        {"symbol": "AAPL", "shares": 10.0, "avg_cost": 148.1, "currency": "USD"},
    ])
    monkeypatch.setattr(ibkr_flex, "update_holding", lambda *a, **k: True)

    result = reconcile_holdings()

    assert [u["symbol"] for u in result["updated"]] == ["AAPL"]


# ---------- _format_summary ----------

def test_format_summary_not_configured_returns_none():
    assert _format_summary({"status": "not_configured", "added": [], "updated": [], "removed": []}) is None


def test_format_summary_fetch_failed():
    msg = _format_summary({"status": "fetch_failed", "added": [], "updated": [], "removed": []})
    assert "Couldn't fetch" in msg


def test_format_summary_fetch_failed_includes_reason():
    result = {"status": "fetch_failed", "added": [], "updated": [], "removed": [],
              "error": "Invalid token (code 1003)"}
    msg = _format_summary(result)
    assert "Reason: Invalid token (code 1003)" in msg


def test_format_summary_skipped_empty():
    msg = _format_summary({"status": "skipped_empty", "added": [], "updated": [], "removed": []})
    assert "zero stock positions" in msg


def test_format_summary_no_changes():
    msg = _format_summary({"status": "ok", "added": [], "updated": [], "removed": []})
    assert "already match" in msg


def test_format_summary_formats_shares_without_trailing_zero():
    result = {
        "status": "ok",
        "added": [{"symbol": "AAPL", "shares": 15.0, "avg_cost": 148.1, "currency": "USD"}],
        "updated": [{
            "symbol": "MSFT",
            "from": {"shares": 10.0, "avg_cost": 400.0},
            "to": {"shares": 12.734, "avg_cost": 400.0},
        }],
        "removed": [],
    }
    msg = _format_summary(result)
    assert "15 @" in msg  # not "15.0 @"
    assert "10→12.734 shares" in msg


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


# ---------- _compare_pricing ----------

def test_compare_pricing_none_when_no_mark_price_data(monkeypatch):
    positions = [{"symbol": "AAPL", "currency": "USD", "mark_price": None, "unrealized_pnl": None}]
    assert _compare_pricing(positions) is None


def test_compare_pricing_computes_totals_and_flags_mismatch(monkeypatch):
    positions = [
        {"symbol": "AAPL", "currency": "USD", "mark_price": 150.0, "unrealized_pnl": 100.0},
        {"symbol": "NVDA", "currency": "USD", "mark_price": 130.0, "unrealized_pnl": 50.0},
    ]
    metrics = {
        "holdings": [
            {"symbol": "AAPL", "current_price": 150.5, "unrealized_gain": 105.0},   # close match, <2%
            {"symbol": "NVDA", "current_price": 140.0, "unrealized_gain": 55.0},    # 140 vs 130 = 7.7% off
        ]
    }
    monkeypatch.setattr(ibkr_flex, "calculate_portfolio_metrics", lambda save_snapshot=True: metrics)
    monkeypatch.setattr(ibkr_flex, "fetch_fx_rate", lambda frm, to: 1.0)

    result = _compare_pricing(positions)

    assert result["bot_total_gain"] == pytest.approx(160.0)
    assert result["ibkr_total_gain"] == pytest.approx(150.0)
    assert [m["symbol"] for m in result["mismatches"]] == ["NVDA"]
    assert result["mismatches"][0]["diff_pct"] == pytest.approx(7.7, abs=0.1)


def test_compare_pricing_converts_currency_to_home(monkeypatch):
    positions = [{"symbol": "D05.SI", "currency": "SGD", "mark_price": 38.0, "unrealized_pnl": 100.0}]
    metrics = {"holdings": [{"symbol": "D05.SI", "current_price": 38.0, "unrealized_gain": 100.0}]}
    monkeypatch.setattr(ibkr_flex, "calculate_portfolio_metrics", lambda save_snapshot=True: metrics)
    monkeypatch.setattr(ibkr_flex, "fetch_fx_rate", lambda frm, to: 1.35)

    result = _compare_pricing(positions)

    assert result["bot_total_gain"] == pytest.approx(135.0)
    assert result["ibkr_total_gain"] == pytest.approx(135.0)


def test_compare_pricing_skips_symbol_bot_could_not_price(monkeypatch):
    positions = [{"symbol": "BAD", "currency": "USD", "mark_price": 100.0, "unrealized_pnl": 10.0}]
    monkeypatch.setattr(ibkr_flex, "calculate_portfolio_metrics", lambda save_snapshot=True: {"holdings": []})
    monkeypatch.setattr(ibkr_flex, "fetch_fx_rate", lambda frm, to: 1.0)

    result = _compare_pricing(positions)

    assert result == {"bot_total_gain": 0.0, "ibkr_total_gain": 0.0, "mismatches": []}


# ---------- _format_summary pricing section ----------

def test_format_summary_includes_pricing_when_present():
    result = {
        "status": "ok", "added": [], "updated": [], "removed": [],
        "pricing": {"bot_total_gain": 160.0, "ibkr_total_gain": 150.0, "mismatches": []},
    }
    msg = _format_summary(result)
    assert "Unrealized gain" in msg
    assert "Bot: +160.00" in msg
    assert "IBKR: +150.00" in msg


def test_format_summary_includes_mismatch_flags():
    result = {
        "status": "ok", "added": [], "updated": [], "removed": [],
        "pricing": {
            "bot_total_gain": 0.0, "ibkr_total_gain": 0.0,
            "mismatches": [{"symbol": "NVDA", "bot_price": 140.0, "ibkr_price": 130.0, "diff_pct": 7.7}],
        },
    }
    msg = _format_summary(result)
    assert "Pricing mismatch" in msg
    assert "NVDA: bot 140.00 vs IBKR 130.00 (Δ7.7%)" in msg


def test_format_summary_omits_pricing_section_when_none():
    result = {"status": "ok", "added": [], "updated": [], "removed": [], "pricing": None}
    msg = _format_summary(result)
    assert "Unrealized gain" not in msg


# ---------- last-reconciled timestamp ----------

def test_get_last_reconciled_at_delegates_to_settings(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "get_setting", lambda key, default=None: "23 Jul 2026, 16:10 SGT")
    assert get_last_reconciled_at() == "23 Jul 2026, 16:10 SGT"


def test_get_last_reconciled_at_none_when_never_run(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "get_setting", lambda key, default=None: default)
    assert get_last_reconciled_at() is None


# ---------- run_reconciliation timestamp persistence ----------

async def _async_noop(*args, **kwargs):
    return True


def test_run_reconciliation_sets_timestamp_only_on_success(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "reconcile_holdings", lambda: {
        "status": "ok", "added": [], "updated": [], "removed": [], "pricing": None,
    })
    monkeypatch.setattr(ibkr_flex, "send_telegram_message", _async_noop)
    set_calls = []
    monkeypatch.setattr(ibkr_flex, "set_setting", lambda k, v: set_calls.append(k))

    asyncio.run(ibkr_flex.run_reconciliation())

    assert set_calls == [ibkr_flex.LAST_RECONCILED_SETTING_KEY]


def test_run_reconciliation_skips_timestamp_on_failure(monkeypatch):
    monkeypatch.setattr(ibkr_flex, "reconcile_holdings", lambda: {
        "status": "fetch_failed", "added": [], "updated": [], "removed": [], "error": "boom", "pricing": None,
    })
    monkeypatch.setattr(ibkr_flex, "send_telegram_message", _async_noop)
    set_calls = []
    monkeypatch.setattr(ibkr_flex, "set_setting", lambda k, v: set_calls.append(k))

    asyncio.run(ibkr_flex.run_reconciliation())

    assert set_calls == []
