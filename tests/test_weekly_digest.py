from datetime import date

from weekly_digest import build_digest, holding_week_moves

PERF = {"currency": "SGD", "start_date": "2026-09-21", "change": 250.0, "change_pct": 1.5}


def test_holding_week_moves_sorted_best_first_and_skips_new_holdings():
    holdings = [
        {"symbol": "AAPL", "current_price": 95.0},
        {"symbol": "NVDA", "current_price": 110.0},
        {"symbol": "NEW", "current_price": 50.0},  # bought mid-week: no start price
    ]
    moves = holding_week_moves(holdings, {"AAPL": 100.0, "NVDA": 100.0})
    assert [s for s, _ in moves] == ["NVDA", "AAPL"]
    assert moves[0][1] == 10.0 and moves[1][1] == -5.0


def test_digest_full():
    upcoming = [("NVDA", {"date": date(2026, 9, 30), "days_until": 2, "timing": "after market close",
                          })]
    msg = build_digest(PERF, 0.5, [("NVDA", 10.0), ("AAPL", -5.0)], upcoming, privacy=False)
    assert "Week in review" in msg
    assert "+S$250.00 (+1.50%)" in msg
    assert "vs S&P 500: +0.50% (you +1.00 pts)" in msg
    assert "Best: NVDA +10.0% · Worst: AAPL -5.0%" in msg
    assert "Earnings next week" in msg and "📅 NVDA" in msg


def test_digest_privacy_and_missing_parts():
    msg = build_digest(PERF, None, [], [], privacy=True)
    assert "250" not in msg and "•••" in msg
    assert "S&P" not in msg and "Best" not in msg and "Earnings" not in msg


def test_digest_includes_income_when_recorded():
    income = {"realized": 140.0, "dividends": 18.2, "currency": "SGD"}
    msg = build_digest(PERF, None, [], [], privacy=False, income=income)
    assert "Realized this week: +S$140.00 · Dividends this week (net): +S$18.20" in msg
