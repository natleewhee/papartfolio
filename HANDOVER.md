# papartfolio — Handover / Requirements

A personal Telegram bot that tracks a stock portfolio: daily P&L reports,
price alerts, support/resistance levels, earnings dates, broker
reconciliation, and an AI-generated market brief. Single-user by design
(one hardcoded `TELEGRAM_USER_ID`), deployed as a long-running process on
Render.

This doc is the handover reference for anyone (human or AI) picking up
this codebase cold: what it does, how it's built, how it's configured, and
what to watch out for.

---

## 1. Purpose

Replace manually checking multiple brokerage/price apps with one Telegram
bot that:
- Reports portfolio value, daily/weekly/monthly P&L, and currency mix once
  a day, plus on demand.
- Pings on market open/close for currencies actually held.
- Alerts on price thresholds and proximity to support levels.
- Tracks a watchlist independent of actual holdings.
- Surfaces upcoming/recent earnings dates.
- Reconciles holdings against a real IBKR account (Flex Web Service) so
  `/add` entries never silently drift from reality.
- Generates a short AI market brief (macro news, holdings that moved
  outside their normal range, and big market news elsewhere) via the
  Claude API with web search.

Non-goals: this is not multi-user, not a trading/execution system (IBKR
Flex is read-only reporting, no order capability), and does not aim for
broad brokerage support beyond IBKR.

---

## 2. Tech Stack

| Concern | Choice | Notes |
|---|---|---|
| Bot framework | `python-telegram-bot` 21.6 | Pinned for Python 3.13 support — see §8 |
| Scheduling | `apscheduler` (AsyncIOScheduler + CronTrigger) | All scheduled jobs live in `main.py` |
| Database | Turso (libsql), via `libsql` driver | Persists across Render restarts, unlike local disk |
| Prices/quotes | Finnhub (US/free tier) + yfinance (SGX `.SI` tickers, history/candles) | Finnhub's international coverage needs a paid plan |
| Broker reconciliation | IBKR Flex Web Service (XML report polling) | Read-only; optional feature |
| AI brief | Anthropic API (`claude-opus-5`, adaptive thinking, `web_search_20260209` tool) | Optional feature |
| Hosting | Render (free-tier Web Service) | See §8 for the two workarounds this requires |
| Runtime | Python 3.13 (pinned via `.python-version`) | |
| Tests | pytest, 150+ tests across `tests/` | No live network calls — everything mocked |

---

## 3. Architecture — module map

```
main.py              Entry point: builds the Application, registers all
                      command handlers, sets up the APScheduler jobs
                      (daily report, price alerts, market open/close pings,
                      IBKR reconciliation), starts a bare HTTP health-check
                      server + self keep-alive pinger for Render.

config.py             All env vars + static config (markets, timezone,
                      report defaults). Required vars crash on startup via
                      _require_env(); optional feature vars (IBKR,
                      Anthropic) default to None and the feature
                      self-disables.

portfolio_db.py       All Turso/libsql access: holdings, daily snapshots,
                      portfolio aggregates, settings (k/v), alerts,
                      watchlist. Schema + migrations live in init_db().

portfolio.py          Pure portfolio math: calculate_portfolio_metrics()
                      (the core aggregation — total value, gain, daily
                      change, FX-converted home-currency total, per-holding
                      breakdown), period performance (/week, /month),
                      currency breakdown, money/share formatting.

fetcher.py            Price/FX/extended-hours fetching. Finnhub for US,
                      yfinance for SGX. Retry wrapper (_retry) reused by
                      support.py and earnings.py.

support.py            Support & resistance level detection from ~1yr of
                      yfinance daily history (swing points + moving
                      averages), short-term (~2mo) and mid-term (~6mo)
                      horizons. History is cached (6hr TTL) since it barely
                      moves intraday. Also used by ai_brief.py for
                      volatility context (see below).

earnings.py           Next/last earnings date + result per symbol.
                      Finnhub calendar (US), yfinance (SGX fallback).

alerts.py             Price threshold alert evaluation, checked every 15
                      min by the scheduler.

market_notifications.py   Market open/close Telegram pings — no-ops if
                      nothing is held in that market's currency.

ibkr_flex.py          IBKR Flex Web Service integration: request → poll →
                      parse XML → diff against the bot's holdings table →
                      apply add/update/remove. Also cross-checks the bot's
                      live pricing against IBKR's own mark price. Silently
                      disabled without IBKR_FLEX_TOKEN/IBKR_FLEX_QUERY_ID.

ai_brief.py            AI market brief generation via the Anthropic API.
                      Filters holdings to only those with a move large
                      relative to their own 20-day volatility, or genuine
                      thesis-relevant news. See §5 for the exact prompt
                      design. Silently disabled without ANTHROPIC_API_KEY.

telegram_handler.py    Assembles and sends the daily report (delegates each
                      optional section — extended hours, earnings, support,
                      Signals, AI brief — to its own _build_*_section()
                      helper so any one of them failing/being disabled
                      doesn't break the rest of the report). Signals
                      (biggest movers, support/resistance proximity, 200
                      EMA proximity across holdings + watchlist) leads the
                      report alongside the AI brief.

bot_handlers.py        All /command handlers (see §6) — the Telegram-facing
                      layer. Talks to portfolio_db.py, portfolio.py,
                      fetcher.py, support.py, earnings.py, ibkr_flex.py,
                      ai_brief.py as needed per command.
```

Import direction is roughly: `main.py` → `bot_handlers.py` /
`telegram_handler.py` → feature modules (`portfolio.py`, `support.py`,
`earnings.py`, `ibkr_flex.py`, `ai_brief.py`, `alerts.py`,
`market_notifications.py`) → `fetcher.py` / `portfolio_db.py` → `config.py`.
No circular imports; `ai_brief.py` reaches into `support.py`'s private
`_fetch_history()` to reuse its cached yfinance history (same pattern as
`support.py` reaching into `fetcher.py`'s private `_retry()`).

---

## 4. Data model (Turso/libsql)

- **holdings** — `symbol` (unique), `shares` (REAL — fractional shares are
  real, from DRIP/IBKR), `avg_cost`, `currency`, manual support-level
  override columns (added via migration), timestamps.
- **daily_snapshots** — per-symbol daily price/value snapshots, used for
  `/week` and `/month` period performance.
- **portfolio_aggregates** — per-day total value/cost-basis snapshot.
- **settings** — generic key/value store (privacy mode, report style,
  report time, IBKR last-reconciled timestamp, etc.) via
  `get_setting`/`set_setting`.
- **alerts** — price threshold alerts (`symbol`, `direction`, `threshold`,
  `active`).
- **watchlist** — symbols tracked for support levels without necessarily
  being held; supports manual ST/MT override prices per symbol.

`init_db()` runs `CREATE TABLE IF NOT EXISTS` for all tables, then applies
additive migrations (`ALTER TABLE ... ADD COLUMN`, swallowing the
"duplicate column" error) for columns added after the original schema —
there is no separate migration runner or version table; every migration is
just an idempotent `ALTER TABLE` at the top of `init_db()`.

---

## 5. Notable design decisions worth knowing before changing things

- **Optional-feature env var pattern**: `IBKR_FLEX_TOKEN`/`IBKR_FLEX_QUERY_ID`
  and `ANTHROPIC_API_KEY` use plain `os.getenv()` (not `_require_env`), so
  their absence doesn't crash the app — the feature's own `is_configured()`
  gates everything downstream. Follow this pattern for any new optional
  integration.
- **AI brief filtering is data-grounded, not vibes-based**: `ai_brief.py`
  computes each holding's 20-trading-day return stdev (via
  `support._fetch_history`) and only asks the model to mention a holding
  if today's move is ~1.5x+ that stock's own normal daily swing, or there's
  genuine thesis-relevant news — not a fixed % cutoff applied identically
  to every symbol. The brief is capped at ~150 words (`MAX_TOKENS = 2000`)
  and leads the daily report (placed before the numbers, not after).
- **Signals uses a flat threshold, deliberately different from the AI
  brief**: `telegram_handler._build_signals_section` flags movers at a flat
  ±3% (`MOVER_THRESHOLD_PCT`), independent of the AI brief's relative-to-
  volatility definition — the two are allowed to diverge rather than share
  a calculation. Support/resistance and 200-EMA (`support.EMA_PERIOD`,
  `NEAR_EMA_THRESHOLD_PCT`) proximity checks cover holdings as well as the
  watchlist, computed once per report (`_signals_population`,
  `_resolve_signals_support`) and shared with the watchlist support table
  below it, so a watchlist symbol isn't quoted or support-checked twice.
  The 200 EMA uses a normalized weighted average (pandas' `ewm(adjust=True)`
  convention) rather than the seed-then-recur convention — the latter needs
  far more post-seed history than the shared 1-year yfinance window
  provides to converge.
- **IBKR Flex is EOD-only, once a day**: IBKR's own Flex "Activity" data
  refreshes once daily at their own close-of-business batch — querying it
  more often just re-reads the same snapshot. Two scheduled passes exist
  (10 min after each market close, plus a fixed `IBKR_RECONCILE_CATCHUP_TIME`
  evening pass, default 20:00 SGT) specifically because the early pass can
  fire before IBKR's own batch has finished and silently re-read the
  *previous* day's snapshot — the fix for that is running at a *better*
  time, not running more often.
- **Reconciliation refuses to wipe holdings on a suspicious empty report**:
  if IBKR's Flex report parses to zero stock positions while the bot
  currently holds something, `reconcile_holdings()` treats that as a likely
  fetch/parse failure and leaves holdings untouched rather than deleting
  everything.
- **Render free-tier workarounds** (`main.py`): a bare HTTP health-check
  server (Render's Web Service port scan needs *something* listening) and
  a self keep-alive pinger (Render idles a Web Service after ~15 min
  without *inbound* traffic; this bot's only traffic is outbound Telegram
  long-polling, so it pings its own `RENDER_EXTERNAL_URL` periodically).
- **`/settime` reschedules live** via `context.application.bot_data["scheduler"]`
  — no restart needed. The IBKR reconcile jobs and market pings are
  currently *not* live-reschedulable this way (they're set once at
  `main()` startup); only the daily report time is.

---

## 6. Command reference

| Command | Purpose |
|---|---|
| `/start`, `/help` | Onboarding / full command list |
| `/add`, `/remove`, `/update`, `/clear` | Manage holdings |
| `/list` (alias `/portfolio`) | Current portfolio table |
| `/sync` | Manually trigger the daily report |
| `/price SYMBOL` | Quick quote lookup |
| `/week`, `/month` | Period performance |
| `/export` | Backup holdings as pasteable `/add` commands |
| `/settings` | View privacy/report-style/report-time + IBKR/AI-brief configured status (with masked key preview) |
| `/schedule` | Full notification + reconciliation schedule |
| `/privacy`, `/reportstyle`, `/settime` | Report preferences |
| `/alert`, `/alerts`, `/unalert`, `/alertsupport` | Price threshold alerts |
| `/support`, `/resistance SYMBOL` | On-demand support/resistance levels |
| `/watch`, `/unwatch`, `/watchlist` | Watchlist management (auto or manual ST/MT levels) |
| `/earnings` | Earnings dates/results for holdings + watchlist |
| `/reconcile` | Manually trigger IBKR reconciliation |
| `/brief` | On-demand AI market brief |

Unknown commands fall through to a "did you mean...?" suggestion handler
(`cmd_unknown`, fuzzy-matched via `difflib`).

---

## 7. Configuration reference

Required (app exits on startup if missing):

| Var | Purpose |
|---|---|
| `FINNHUB_API_KEY` | US quotes/earnings calendar |
| `TELEGRAM_BOT_TOKEN` | Bot auth |
| `TELEGRAM_USER_ID` | The single authorized user's Telegram ID |
| `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` | Database |

Optional (feature self-disables if unset):

| Var | Enables |
|---|---|
| `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID` | IBKR reconciliation (`/reconcile`, scheduled passes). Both required together. Set up via IBKR Account Management → Reports → Flex Queries (new Activity/Open Positions query with Symbol, Position, Cost Basis Price, Currency, Asset Category columns) → Reports → Settings → Flex Web Service for the token. |
| `ANTHROPIC_API_KEY` | AI market brief (`/brief`, daily report section) |

See `.env.example` for the template. **Render-specific gotcha**: an env
var change on Render only takes effect after the next completed deploy —
setting a variable without a subsequent redeploy leaves the currently
running process unaware of it. `/settings` shows the AI brief's
actual live-process status (configured + masked key preview) precisely to
make this diagnosable without dashboard access.

Static config (`config.py`, code changes only): `TIMEZONE` (Asia/Singapore),
`DAILY_REPORT_TIME` default (20:30 SGT — startup default only, `/settime`
overrides at runtime), `HOME_CURRENCY` (SGD), `IBKR_RECONCILE_CATCHUP_TIME`
(20:00 SGT), `MARKETS` (US/SG open-close times + timezones).

---

## 8. Scheduled jobs (`main.py`, all via APScheduler)

| Job | Schedule | Notes |
|---|---|---|
| Daily report | `DAILY_REPORT_TIME` (default 20:30 SGT), weekdays | Live-reschedulable via `/settime` |
| Price alert check | Every 15 min | |
| Market open/close pings | Per-market open/close time (own timezone), weekdays | Open pings fire +30s (quote-feed lag); no-op if nothing held in that currency |
| IBKR reconcile (early) | 10 min after each market's close, that market's own timezone | Best-effort; may re-read the previous day's snapshot (see §5) |
| IBKR reconcile (catch-up) | `IBKR_RECONCILE_CATCHUP_TIME` (default 20:00 SGT), weekdays | Added because the early pass can be too early relative to IBKR's own EOD batch |

---

## 9. Deployment (Render)

- Runtime pinned to Python 3.13 via `.python-version`.
- `requirements.txt` pins `python-telegram-bot==21.6` specifically because
  20.x never declared/tested Python 3.13 support and crashes on
  `Application.builder().build()` there (`AttributeError` on a `__slots__`
  attribute) — do not downgrade this without re-verifying 3.13
  compatibility. It also carries the `httpx~=0.27` pin that `anthropic`'s
  `httpx>=0.25` requirement needs; earlier PTB versions pin an
  incompatible `httpx~=0.24`, which breaks `pip install` outright
  (`ResolutionImpossible`) once `anthropic` is added as a dependency.
- No CI pipeline configured in this repo — verification before merging is
  manual: `python -m py_compile` + `python -m pytest tests/ -q` + (for
  dependency/runtime changes) a real clean-venv install under the target
  Python version.
- Deploys are triggered by pushes to `main`; there's no staging
  environment.

---

## 10. Testing

`pytest tests/` — one test file roughly per module, all mocked (no real
network/API calls, no real Telegram/Turso/IBKR/Anthropic connections).
Known flaky spot: 5 tests in `test_earnings.py` intermittently fail on a
`days_until` off-by-one — root cause not fully chased down, but it's a
`date.today()` (test, system-local) vs `earnings._today()` (module,
SGT-aware) day-boundary mismatch depending on what time/timezone the test
runner itself executes in. Confirmed pre-existing and unrelated to any
change made in recent PRs (reproduced identically on `main` via
`git stash`). Worth a real fix (make the test inject/mock "today" instead
of relying on wall-clock time) if it keeps tripping people up.

---

## 11. Known limitations / open items

- Single-user only — `TELEGRAM_USER_ID` is hardcoded, no multi-tenancy.
- IBKR integration is Flex-only (EOD, read-only); no live/real-time broker
  feed, and no support for brokers other than IBKR.
- No holiday calendar for market open/close pings or reconciliation
  scheduling — a market holiday just means those jobs no-op harmlessly
  (no holdings priced that day) rather than being skipped intelligently.
- No staging environment or CI; verification is manual before every push.
- AI brief quality depends entirely on the model's web search results for
  that day — no fallback content if search comes back thin.
- `test_earnings.py`'s day-boundary flakiness (see §10) hasn't been root-caused.

---

## 12. Where to look first for common tasks

- **Add a new `/command`**: `bot_handlers.py` (handler function) +
  `main.py` (`CommandHandler` registration + `BOT_COMMANDS` list for
  Telegram autocomplete) + `/help` text in `bot_handlers.py`.
- **Add a new optional integration**: follow the `IBKR_FLEX_TOKEN` /
  `ANTHROPIC_API_KEY` pattern in `config.py` — plain `os.getenv()`, an
  `is_configured()` guard in the new module, silent no-op everywhere it's
  consumed.
- **Change daily report content/order**: `telegram_handler.py`'s
  `send_daily_report()` — each section is built by its own
  `_build_*_section()` helper and appended conditionally.
- **Change scheduled job timing**: `main.py`, `scheduler.add_job(...)` calls.
