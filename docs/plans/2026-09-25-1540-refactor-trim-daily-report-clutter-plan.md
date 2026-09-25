---
title: Trim Daily Report to Core Sections - Plan
type: refactor
date: 2026-09-25
topic: trim-daily-report-clutter
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# Trim Daily Report to Core Sections

## Goal Capsule

- **Objective:** The daily report and its related on-demand commands
  (`/list`, `/support`/`/resistance`, `/settings`/`/schedule`) are shorter,
  less redundant with each other, and easier to scan — carrying only what
  earns its place daily, with the rest reachable on demand.
- **Product authority:** The Product Contract below is authoritative —
  exactly the sections and command changes named here happen, no others.
- **Open blockers:** None.

## Product Contract

### Summary

The daily report drops the Watchlist Support Levels table, the Extended
Hours (pre/post-market) section, and the AI Market Brief; the Earnings
Watch section folds into Signals instead of standing alone; and the
holdings table drops Price/Cost in favor of a $ change column beside
%CHG. `/list` adopts that same compact table. `/support` and
`/resistance` merge into one command. `/settings` stops duplicating
`/schedule`'s report time. The on-demand commands behind every cut
section stay fully functional.

### Requirements

**Report section removal**

- R1. The daily report no longer includes the Watchlist Support Levels
  table.
- R2. The daily report no longer includes the Extended Hours (pre/post-
  market) section.
- R3. The daily report no longer includes the AI Market Brief section.
- R4. `/watchlist`, `/support`, `/price`, and `/brief` remain fully
  functional and unchanged — all three cut sections' data stays reachable
  on demand.
- R5. This is a permanent code removal, not a settings toggle.

**Signals absorbs Earnings**

- R6. Signals includes each qualifying upcoming (within 14 days) or
  recent (within 3 days) earnings event as its own line, using the same
  criteria the standalone Earnings Watch section used.
- R7. The daily report no longer has a separate Earnings Watch section —
  its content only appears within Signals.
- R8. Earnings lines in Signals are computed from the same holdings-
  plus-watchlist population Signals already resolves, not a separate
  fetch.

**Holdings table columns**

- R9. The holdings table drops the PRICE and COST columns.
- R10. The holdings table adds a $CHG column (today's dollar change, in
  the holding's own currency) immediately before %CHG.

**`/list` presentation**

- R11. `/list` renders holdings with the same compact table (R9, R10)
  the daily report uses, instead of its own verbose multi-line-per-
  holding block. The totals block below it (Value, Cost Basis, Gain,
  Today, Currency Mix, FX) is unchanged.

**`/support` + `/resistance` merge**

- R12. `/support SYMBOL` shows both support and resistance levels
  (short-term and mid-term for each) in one reply.
- R13. `/resistance` is removed as a separate command; R12 fully covers
  its behavior.

**`/settings` + `/schedule` dedup**

- R14. `/settings` no longer states the daily report's exact time —
  it points to `/schedule` instead, which already shows the time plus
  its day range.

**Housekeeping**

- R15. `/help` reflects all of the above: no `/resistance` entry,
  `/support`'s entry describes both support and resistance, `/brief`'s
  entry no longer claims report inclusion.
- R16. `HANDOVER.md` reflects the new report structure and command set.

### Key Decisions

- **Hard-remove rather than a togglable setting** (session-settled:
  user-directed — chosen over a `/reportstyle`-style toggle: simpler, and
  the on-demand commands already cover the reversibility case). Governs
  R5.
- **Earnings folds into Signals rather than staying a separate section**
  (session-settled: user-directed — both already shared the same
  "flag-only, silent otherwise" design; Earnings previously trailed after
  the holdings table, away from the rest of "what needs attention
  today"). Governs R6, R7.
- **Earnings lines reuse Signals' existing shared population** rather
  than a separate fetch, continuing the same compute-once-and-share
  pattern already used for prices and support levels in this section
  (session-settled: user-directed). Governs R8.
- **Holdings table drops Price/Cost and adds $CHG before %CHG**
  (session-settled: user-directed, refined through an example mockup —
  dropping Price/Cost freed the width $CHG needed, and $CHG-before-%CHG
  matches the "$ then %" convention already used in the report's Today
  line). Governs R9, R10.
- **`/list` adopts the report's compact table** rather than its own
  verbose format (session-settled: user-directed — `/list` was in fact
  the densest surface in the bot, worse than the report it was modeled
  after). Governs R11.
- **`/support` absorbs `/resistance`**, not the reverse, since support is
  the more frequently reached-for of the two (session-settled:
  user-approved — proposed with the tradeoff surfaced, user assented).
  Governs R12, R13.
- **`/settings` drops the exact time in favor of pointing to `/schedule`**,
  not the reverse, since `/schedule` carries strictly more information
  (time and day range) (session-settled: user-approved — proposed with
  the tradeoff surfaced, user assented). Governs R14.

### Acceptance Examples

- AE1. **Covers R6, R7, R8.** Given NVDA reports earnings in 3 days and
  no other signal qualifies, Signals shows only the movers/support/EMA
  lines that qualify (if any) plus one earnings line for NVDA; no
  separate Earnings Watch section appears anywhere in the report.
- AE2. **Covers R9, R10.** Given a holding down $538 and -3.5% today,
  the table shows `-$538` and `-3.5%` as adjacent columns, with no PRICE
  or COST columns present.
- AE3. **Covers R11.** Given `/list` is run against the same holdings as
  a daily report, the rendered table matches what the daily report would
  show for that holdings set.
- AE4. **Covers R12, R13.** Given `/support AAPL` is run, the reply
  includes both a support subsection and a resistance subsection; a
  `/resistance AAPL` command falls through to the unknown-command
  handler.
- AE5. **Covers R14.** Given `/settings` is run, it no longer states an
  exact report time, but does reference `/schedule` for it.

### Scope Boundaries

- Out of scope: removing or deprecating `ai_brief.py`,
  `support.py`'s support/resistance computation, or
  `fetcher.fetch_extended_hours` — these stay intact for the on-demand
  commands in R4. Only the daily report's own calls into them are
  removed.
- Out of scope: the Signals section's own near-support-level and 200 EMA
  checks, which use a separate code path
  (`_resolve_signals_support`/`near_ema200_flags`) from the Watchlist
  Support Levels table being cut — unaffected by this plan.
- Out of scope: `resolve_support_levels`'s manual ST/MT override support
  (set via `/watch`) extending to resistance — resistance has never had a
  manual-override path, and this plan doesn't add one.

## Planning Contract

### Key Technical Decisions

- KTD1. **`_build_signals_section` absorbs `_build_earnings_section`'s
  logic directly** rather than having `send_daily_report` call both and
  concatenate — Signals needs the earnings lines inside its own `"📡
  *Signals*"` block, not as a trailing sibling section. The standalone
  `_build_earnings_section` function and its call site in
  `send_daily_report` are deleted. Governs R6, R7.
- KTD2. **Earnings lines render as `"📅 " + format_earnings_line(...)`**,
  one line per qualifying symbol — matching the existing per-symbol
  richness (date, timing, EPS estimate/actual, surprise%) that doesn't
  compress into a single comma-separated summary line the way movers/
  support/EMA do. Placed last within Signals, after the movers/support/
  EMA lines. Governs R6.
- KTD3. **$CHG uses `daily_change_%`'s existing per-holding
  `daily_change_$` field** (already computed by
  `calculate_portfolio_metrics`, in the holding's own currency) via
  `fmt_money(..., show_sign=True, decimals=0)` — no new computation,
  same masking-under-privacy behavior as VALUE for free. Governs R10.
- KTD4. **The merged `/support` calls both `resolve_support_levels` and
  `compute_resistance_levels` independently** rather than unifying them
  into one function — they already have different signatures (support
  takes manual ST/MT overrides; resistance doesn't, see Scope
  Boundaries), and unifying them is a larger refactor this plan doesn't
  need. Governs R12.

## Implementation Units

### U1. Fold Earnings into Signals

- **Goal:** Earnings lines appear inside Signals; the standalone
  Earnings Watch section is gone.
- **Requirements:** R6, R7, R8.
- **Files:** `telegram_handler.py`, `tests/test_telegram_handler.py`.
- **Approach:**
  - In `_build_signals_section`, after the EMA line, compute
    `fetch_earnings_bulk([r["symbol"] for r in population])`, then
    `earnings_flags(results)` for `(upcoming, recent)`, then one
    `"📅 " + format_earnings_line(symbol, get_currency_for_symbol(symbol), fmt_money, next_event=...)`
    line per upcoming entry and `last_event=...` per recent entry (KTD2).
  - Delete `_build_earnings_section` and its call site
    (`earnings_section = await asyncio.to_thread(...)`) in
    `send_daily_report`.
  - Keep the `fetch_earnings_bulk, earnings_flags, format_earnings_line`
    import — now consumed by `_build_signals_section` instead.
- **Test Scenarios:**
  - AE1: an upcoming-earnings-only day renders the earnings line inside
    Signals' output, with no other section for it.
  - A day with an upcoming and a recent earnings event both present:
    both lines appear, upcoming before recent (matching the existing
    `earnings_flags` order).
  - A day with no signals of any kind (movers, support, EMA, earnings)
    returns `""` — Signals stays fully omittable.
- **Verification:** `python -m pytest tests/test_telegram_handler.py -q`.

### U2. Update holdings table columns

- **Goal:** The holdings table shows VALUE, GAIN%, $CHG, %CHG — no
  PRICE or COST.
- **Requirements:** R9, R10.
- **Files:** `portfolio.py`, `tests/test_portfolio.py`.
- **Approach:**
  - In `format_holdings_table`, change the header to
    `f"{'SYMBOL':<7}{'VALUE':>10}{'GAIN%':>8}{'$CHG':>9}{'%CHG':>8}"` and
    drop the `price_str`/`cost_str` locals.
  - Add `chg_dollar_str = fmt_money(h["daily_change_$"], currency, privacy, show_sign=True, decimals=0)`
    (KTD3) and place it between `gain_str` and `pct_str` in the row
    format string.
- **Test Scenarios (update existing):**
  - `test_format_holdings_table_contains_header_and_rows`: add
    `daily_change_$` to both fixture holdings; assert `PRICE`/`COST` are
    absent from the header; assert the $CHG value renders with a sign.
  - `test_format_holdings_table_privacy_masks_dollar_amounts_only`: add
    `daily_change_$` to the fixture; assert the $CHG value is masked too.
- **Verification:** `python -m pytest tests/test_portfolio.py -q`.

### U3. Switch `/list` to the compact table

- **Goal:** `/list` renders the same table U2 produces instead of its
  own verbose per-holding block.
- **Requirements:** R11.
- **Files:** `bot_handlers.py`, `tests/test_bot_handlers.py`.
- **Approach:**
  - In `cmd_list`, replace the per-holding loop building `msg` with
    `"\n" + f"```\n{format_holdings_table(metrics['holdings'], privacy)}\n```"`,
    keeping the header line, the `failed_symbols`/`fx_warnings`
    warnings, and the `Portfolio Total` block (Value, Cost Basis, Gain,
    Today, Currency Mix, FX) exactly as they are today.
  - Import `format_holdings_table` from `portfolio` in `bot_handlers.py`
    (check it isn't already imported before adding).
- **Test Scenarios:**
  - AE3: given the same `holdings` metrics dict, `/list`'s rendered
    table matches `format_holdings_table`'s own output for that data.
  - The totals block (Value/Cost Basis/Gain/Today) still appears
    unchanged below the table.
- **Verification:** `python -m pytest tests/test_bot_handlers.py -q`.

### U4. Merge `/resistance` into `/support`

- **Goal:** One command, `/support SYMBOL`, shows both support and
  resistance; `/resistance` no longer exists.
- **Requirements:** R12, R13.
- **Files:** `bot_handlers.py`, `main.py`, `tests/test_bot_handlers.py`.
- **Approach:**
  - In `cmd_support`, after resolving support data (existing
    `resolve_support_levels` call, unchanged — keeps the manual ST/MT
    override path), also call
    `compute_resistance_levels(symbol, current_price)` (KTD4). If
    resistance data is unavailable, still show support alone rather than
    failing the whole command (mirrors how each horizon already degrades
    to `"n/a"` independently).
  - Render both in one reply: a Support subsection (existing `leg()`
    helper and copy) and a Resistance subsection (existing `leg()`
    logic from the old `cmd_resistance`, with its own `+`/rise wording).
  - Delete `cmd_resistance`.
  - Remove `CommandHandler("resistance", cmd_resistance)` and the
    `cmd_resistance` import in `main.py`.
  - Remove the `("resistance", "Resistance levels for one stock")` entry
    from `BOT_COMMANDS`.
- **Test Scenarios:**
  - AE4: `/support AAPL` with both support and resistance data available
    shows both subsections.
  - `/support AAPL` with resistance unavailable (e.g. insufficient
    history) still shows the support subsection.
  - `main.py` no longer registers a `resistance` command (import-level
    check via `python -c "import main"` with dummy env vars, per this
    repo's existing verification convention).
- **Verification:** `python -m pytest tests/test_bot_handlers.py -q`.

### U5. Dedup `/settings` / `/schedule`

- **Goal:** `/settings` stops repeating `/schedule`'s report time.
- **Requirements:** R14.
- **Files:** `bot_handlers.py`.
- **Approach:** In `cmd_settings`, replace the
  `f"Report time: {report_time} {TIMEZONE} (/settime)\n"` line with a
  pointer, e.g. `"Daily report: see /schedule for time & days (/settime to change)\n"`.
- **Test Scenarios:** None beyond manual inspection — this is a copy-only
  change with no branching logic.
- **Verification:** `python -m py_compile bot_handlers.py`.

### U6. Housekeeping: `/help` and `HANDOVER.md`

- **Goal:** Documentation matches the shipped behavior.
- **Requirements:** R15, R16.
- **Files:** `bot_handlers.py`, `HANDOVER.md`.
- **Approach:**
  - In `cmd_help`, remove the `/resistance` line, update `/support`'s
    line to mention resistance too, and remove "(also included in the
    daily report)" from `/brief`'s line.
  - In `HANDOVER.md`: update `telegram_handler.py`'s module description
    (§3) to drop the removed sections and mention Earnings living inside
    Signals; update the command reference table (§6) to drop
    `/resistance` and reword `/support`; update the Signals design-
    decision bullet (§5) to mention the earnings fold and the table
    column change; update the command count/list anywhere else it's
    enumerated.
- **Verification:** Manual read-through — no test coverage for prose.

### U7. Remove Support Levels, Extended Hours, and AI Brief from the report

- **Goal:** The three originally-agreed sections are gone from
  `send_daily_report`'s output; nothing else changes.
- **Requirements:** R1, R2, R3, R4, R5.
- **Files:** `telegram_handler.py`, `tests/test_telegram_handler.py`.
- **Approach:**
  - In `send_daily_report`, delete the three call sites that append
    `_build_support_section`, `_build_extended_hours_section`, and
    `_build_ai_brief_section` output to `report`.
  - Delete `_build_support_section`, `_build_extended_hours_section`,
    and `_build_ai_brief_section` themselves, along with any imports
    that become unused as a result (e.g. `resolve_support_levels_bulk`,
    `format_support_table`, `near_support_flags`, `format_near_support_line`
    from `support` stay — still used by `_build_signals_section`/
    `_resolve_signals_support`; `generate_market_brief`,
    `is_configured as ai_brief_configured` from `ai_brief` do not and
    should be removed; `fetch_extended_hours_bulk` from `fetcher` does
    not and should be removed).
  - `_signals_population`/`_resolve_signals_support` and the `population`/
    `support_results`/`watchlist_rows` values computed once in
    `send_daily_report` are unaffected — they're consumed by Signals
    (U1) and nothing else after this unit.
  - `/watchlist`, `/support`, `/price`, and `/brief` (R4) are untouched —
    they call `resolve_support_levels`/`resolve_support_levels_bulk`,
    `fetch_extended_hours`, and `ai_brief.generate_market_brief`
    directly, not through the deleted `_build_*_section` functions.
- **Test Scenarios:**
  - `send_daily_report`'s output for a holdings set with no watchlist
    entries no longer contains "Watchlist — Support Levels",
    "Pre/Post-Market", or "AI Market Brief" text.
  - `/brief`, `/support`, `/watchlist`, and `/price` still work
    (existing tests for `ai_brief.py`/`support.py` are untouched by this
    unit and continue to pass).
- **Verification:** `python -m pytest tests/test_telegram_handler.py -q`.

## Verification Contract

| Command | Applies to |
|---|---|
| `python -m py_compile *.py tests/*.py` | All units — syntax/import sanity |
| `python -m pytest tests/ -q` | Full suite — no regressions |
| `FINNHUB_API_KEY=x TELEGRAM_BOT_TOKEN=x TELEGRAM_USER_ID=1 TURSO_DATABASE_URL=x TURSO_AUTH_TOKEN=x python -c "import main"` | U4 — confirms `/resistance`'s removal doesn't break startup wiring |

The full-suite run may show `test_earnings.py`'s pre-existing date-
boundary flakiness (see `HANDOVER.md` §10) — unrelated to this change.

## Definition of Done

- R1-R16 hold under AE1-AE5.
- `python -m pytest tests/ -q` passes with no new failures.
- No dead code remains from the removed sections/commands
  (`_build_earnings_section`, `_build_support_section`'s report call
  site, `_build_extended_hours_section`'s report call site,
  `_build_ai_brief_section`'s report call site, `cmd_resistance`, the
  old `/list` per-holding loop, the old `Report time` line in
  `/settings`).
- `/help` and `HANDOVER.md` match the shipped command set and report
  structure.

