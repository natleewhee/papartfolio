---
title: Daily Report Signals Section - Plan
type: feat
date: 2026-09-02
topic: daily-report-signals-section
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# Daily Report Signals Section

## Goal Capsule

- **Objective:** A reader can tell, within a few seconds of opening the
  daily report, whether anything about their holdings or watchlist needs
  attention today — without reading through the numeric breakdown first.
- **Product authority:** The Product Contract below is authoritative.
  Planning should not re-litigate the signal thresholds or the
  holdings-plus-watchlist population scope decided here.
- **Open blockers:** None.

## Product Contract

### Summary

The daily report gains a consolidated `Signals` section that surfaces
today's notable items — big movers, proximity to support/resistance, and
proximity to the 200-day EMA — in one place near the top, replacing the
scattered best/worst line and near-support flag line that exist today.

### Requirements

**Structure & placement**

- R1. The daily report includes a new `Signals` section that consolidates
  today's notable items, replacing the existing best/worst emoji line and
  the separate near-support flag line.
- R2. `Signals` is placed near the top of the report, alongside the AI
  brief that already leads it, ahead of the numeric summary.
- R9. When nothing qualifies for any of the three signal types below, the
  `Signals` section (including its header) is omitted entirely rather than
  shown empty.

**Movers**

- R3. `Signals` lists every held or watchlisted symbol whose daily %
  change is ±3% or greater, with its symbol and % change.
- R4. When no symbol qualifies as a mover, the movers line is omitted
  rather than shown empty.

**Support/resistance proximity**

- R5. `Signals` lists every held or watchlisted symbol within 5% of a
  short-term or mid-term support/resistance level. This extends today's
  watchlist-only support/resistance computation to holdings as well.
- R6. When no symbol qualifies, the support/resistance line is omitted
  rather than shown empty.

**200 EMA proximity**

- R7. `Signals` separately lists every held or watchlisted symbol within
  5% of its 200-day EMA, computed independently of the existing
  support/resistance detection (which is SMA-based, not EMA-based).
- R8. When no symbol qualifies, the EMA line is omitted rather than shown
  empty.

**Compatibility**

- R10. `Signals` contains only percentages and price-level distances,
  never dollar amounts, so privacy mode (`/privacy`) requires no
  additional masking logic.

### Key Decisions

- **Presentation, not scheduling, is this plan's focus** (session-settled:
  user-directed — chosen over covering both in one plan: keeps one
  coherent work unit per artifact; smarter scheduling is deferred, see
  Scope Boundaries).
- **One consolidated `Signals` section replaces the scattered mentions**,
  rather than emphasizing each existing line individually (session-settled:
  user-directed). Governs R1.
- **Movers use a flat ±3% threshold**, not the AI brief's relative-to-own-
  volatility definition, keeping the two features simple and independent
  rather than sharing a calculation (session-settled: user-directed).
  Governs R3.
- **Support/resistance and EMA checks extend to holdings**, not just
  watchlist symbols, trading more yfinance history calls per report for
  full coverage (session-settled: user-directed). Governs R5, R7.
- **200 EMA is a separate, independent signal**, not folded into the
  existing SMA-based support/resistance candidate list, so it renders as
  its own line rather than silently changing which level wins as nearest
  support/resistance (session-settled: user-directed). Governs R7.

Key Flows are omitted: this is an output-shape/policy change to an
existing report-generation path, not new multi-step user-facing behavior —
Requirements and Acceptance Examples below fully describe what renders and
when.

### Acceptance Examples

- AE1. **Covers R3, R4.** Given NVDA is +3.1% today and no other held or
  watchlisted symbol moved ±3% or more, the movers line shows only NVDA.
  Given no symbol moved ±3% or more, the movers line does not appear.
- AE2. **Covers R5, R6.** Given AAPL is a holding (not on the watchlist)
  and sits within 5% of its 200-day SMA, the support/resistance line
  includes AAPL — even though support levels were previously
  watchlist-only.
- AE3. **Covers R7, R8.** Given TSLA is within 5% of its 200-day EMA but
  not within 5% of any SMA-based support/resistance level, the EMA line
  includes TSLA independently of the support/resistance line.
- AE4. **Covers R9.** Given no symbol qualifies for movers, support/
  resistance, or EMA proximity on a quiet day, the `Signals` section
  (including its header) does not appear in the report.
- AE5. **Covers R10.** Given privacy mode is ON, `Signals` renders
  identically to privacy mode OFF, since it carries no dollar figures.

### Success Criteria

- A reader can scan `Signals` in a few seconds and know whether anything
  needs attention today, without reading the full holdings table or other
  sections first.

### Scope Boundaries

- Deferred for later: smarter scheduling (market-holiday awareness,
  rethinking when reports/reconciliation/pings fire) — a separate future
  area raised in the same conversation, not part of this plan.
- Deferred for later: surfacing action-needed items (triggered alerts,
  IBKR reconciliation changes) and pulling the AI brief's own
  thesis-verdict language further forward — considered and not selected
  for this round.
- Out of scope: changing the AI brief's own movers definition or
  threshold. The AI brief's relative-to-volatility definition and
  `Signals`' flat ±3% threshold are intentionally allowed to diverge (see
  Key Decisions).

## Planning Contract

### Key Technical Decisions

- KTD1. **200 EMA is computed from the same cached 1-year yfinance history
  `support._fetch_history` already fetches for support levels**, via a
  standard SMA-seeded exponential moving average over the available ~252
  daily closes. This is not a new limitation: the existing 200-day SMA
  candidate in `support._compute_levels` relies on the same 1-year window.
  Governs R7.
- KTD2. **`Signals`' support/resistance and EMA checks recompute the full
  holdings-plus-watchlist population independently of
  `telegram_handler._build_support_section`'s own watchlist-only
  computation**, rather than sharing one bulk call across both. Duplicate
  per-symbol yfinance fetches are served from `support.py`'s existing
  6-hour history cache (`_history_cache`), so the duplication costs a
  cheap in-process recompute, not a network round trip. Chosen over a
  shared-population refactor of `_build_support_section`, which would
  widen this plan's blast radius for no user-visible benefit. Governs R5,
  R7.
- KTD3. **`MOVER_THRESHOLD_PCT`, `EMA_PERIOD`, and `NEAR_EMA_THRESHOLD_PCT`
  are plain module constants**, not `config.py` or DB-backed settings —
  matches the existing `support.NEAR_SUPPORT_THRESHOLD_PCT` precedent,
  which is also a hardcoded constant with no user-facing override.

### Assumptions

- A symbol with less than 200 days of yfinance history (a recent IPO) has
  no 200 EMA — `compute_200ema_distance` returns `None` for it, the same
  way `compute_support_levels` already returns `None` for insufficient
  history, and it's silently excluded from the EMA line, never an error.

## Implementation Units

### U1. Add 200 EMA proximity detection to `support.py`

- **Goal:** A new, independent check for "symbol is within 5% of its
  200-day EMA," mirroring the existing near-support-level check in shape.
- **Requirements:** R7, R8.
- **Files:** `support.py`, `tests/test_support.py`.
- **Approach:**
  - Add constants `EMA_PERIOD = 200` and `NEAR_EMA_THRESHOLD_PCT = 5.0`.
  - Add `_ema(values, period)`: an SMA-seeded exponential moving average
    (seed = mean of the first `period` values, then apply the standard
    smoothing multiplier `2 / (period + 1)` to the rest), returning `None`
    when `len(values) < period` — same guard style as the existing `_sma`.
  - Add `compute_200ema_distance(symbol, current_price=None)`: fetches
    history via the existing `_fetch_history(symbol)`, falls back to the
    latest close for `current_price` when not given (matching
    `_compute_levels`'s existing fallback), returns `None` on insufficient
    history or a non-positive price, else
    `{"ema": round(..., 2), "distance_pct": round(..., 2)}`.
  - Add `near_ema200_flags(rows, threshold=NEAR_EMA_THRESHOLD_PCT)`: takes
    `[{"symbol", "current_price"}, ...]`, returns
    `[(symbol, distance_pct), ...]` sorted closest-first, only entries
    within `threshold` — same signature shape as `near_support_flags`.
  - Add `format_near_ema_line(flags)`: renders `"📊 Near 200 EMA: SYM
    (X.X%), ..."` or `""` when empty — mirrors `format_near_support_line`.
- **Test Scenarios:**
  - Insufficient history (`< 200` rows) → `compute_200ema_distance`
    returns `None`.
  - A synthetic alternating-return series with a computable EMA (same
    fixture style as `tests/test_ai_brief.py`'s `_FakeDF`) → verify the
    computed EMA and `distance_pct` against a hand-computed expected value.
  - A `current_price` inside vs. outside `NEAR_EMA_THRESHOLD_PCT` of a
    known EMA → included vs. excluded from `near_ema200_flags`.
  - Empty flags list → `format_near_ema_line` returns `""`.
- **Verification:** `python -m pytest tests/test_support.py -q`.

### U2. Add the `Signals` section to the daily report

- **Goal:** One consolidated, top-of-report section covering movers,
  support/resistance proximity, and 200 EMA proximity across holdings and
  watchlist, replacing the current scattered mentions.
- **Requirements:** R1, R2, R3, R4, R5, R6, R9, R10.
- **Files:** `telegram_handler.py`, `tests/test_telegram_handler.py`.
- **Approach:**
  - Add constant `MOVER_THRESHOLD_PCT = 3.0` near the top of
    `telegram_handler.py`.
  - Add `_signals_population(metrics)`: returns one row per symbol in
    `{watchlist symbols} ∪ {holding symbols}`, each
    `{"symbol", "current_price", "daily_change_%", "manual_st", "manual_mt"}`
    — holdings source `current_price`/`daily_change_%` directly from
    `metrics["holdings"]`; watchlist-only symbols resolve via
    `get_prices_bulk`, same pattern `_build_support_section` already uses
    at telegram_handler.py:111-116; `manual_st`/`manual_mt` come from
    `get_watchlist()` for watchlist symbols, `None` otherwise.
  - Add `_build_signals_section(metrics)`:
    - Movers line: rows with `abs(daily_change_%) >= MOVER_THRESHOLD_PCT`,
      sorted by `abs(daily_change_%)` descending, rendered
      `"🚀 Movers: SYM +X.X%, SYM2 -Y.Y%"`; `""` when none (R3, R4).
    - Support/resistance line: build `resolve_support_levels_bulk` rows
      from the population (same call shape as
      `_build_support_section`'s existing `results = resolve_support_levels_bulk(...)`
      at telegram_handler.py:118-120, population-only difference), then
      `format_near_support_line(near_support_flags(rows))` (R5, R6).
    - EMA line: `format_near_ema_line(near_ema200_flags(rows))` from U1,
      same population (R7, R8).
    - Return `""` when all three lines are empty (R9); otherwise
      `"📡 *Signals*\n" + "\n".join(non-empty lines)`.
  - In `_build_support_section`, remove its own
    `flag_line = format_near_support_line(near_support_flags(rows)); if flag_line: section += ...`
    block (telegram_handler.py:143-145) — the detailed ST/MT table itself
    is unchanged, only the flag line moves into `Signals`.
  - In `send_daily_report`, remove the `🏆/📉` best/worst line
    (telegram_handler.py:249-252), call
    `signals_section = await asyncio.to_thread(_build_signals_section, metrics)`
    alongside the existing `ai_brief_section` call, and prepend
    `signals_section` (when non-empty) directly after `ai_brief_section`,
    before the `Total`/`Today`/`Gain` block.
  - Import `near_ema200_flags, format_near_ema_line` from `support` at the
    top of `telegram_handler.py`.
- **Test Scenarios (Acceptance Examples):**
  - AE1: a mover-only day renders only the movers line.
  - AE2: a holding not on the watchlist appears in the support/resistance
    line.
  - AE3: a symbol near its 200 EMA but not near any support/resistance
    level appears only in the EMA line.
  - AE4: nothing qualifies → `_build_signals_section` returns `""` and
    `send_daily_report` emits no `Signals` header at all.
  - AE5: privacy mode ON produces byte-identical `Signals` output to
    privacy mode OFF (no `fmt_money`/dollar formatting involved).
- **Verification:** `python -m pytest tests/test_telegram_handler.py -q`.

## Verification Contract

| Command | Applies to |
|---|---|
| `python -m py_compile support.py telegram_handler.py tests/test_support.py tests/test_telegram_handler.py` | Both units — syntax/import sanity |
| `python -m pytest tests/test_support.py tests/test_telegram_handler.py -q` | Both units — new + existing behavior |
| `python -m pytest tests/ -q` | Full suite — no regressions elsewhere |

The full-suite run may show `test_earnings.py`'s pre-existing date-boundary
flakiness (see `HANDOVER.md` §10) — unrelated to this change; do not chase
it as part of this plan's verification.

## Definition of Done

- R1-R10 hold under AE1-AE5.
- `python -m pytest tests/ -q` passes with no new failures beyond the
  pre-existing `test_earnings.py` flakiness.
- The removed best/worst line and the old flag-line placement leave no
  dead code behind in `telegram_handler.py`.
- `HANDOVER.md`'s daily-report section description (§3, §5) mentions the
  `Signals` section in place of the old best/worst-line description.
