---
title: Trim Daily Report to Core Sections - Plan
type: refactor
date: 2026-09-25
topic: trim-daily-report-clutter
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# Trim Daily Report to Core Sections

## Goal Capsule

- **Objective:** The daily report is shorter and easier to scan, carrying
  only what earns a daily push; information that's still useful but not
  daily-relevant stays reachable on demand.
- **Product authority:** The Product Contract below is authoritative —
  exactly these three sections are cut, no others.
- **Open blockers:** None.

## Product Contract

### Summary

The daily report drops the Watchlist Support Levels table, the Extended
Hours (pre/post-market) section, and the AI Market Brief — leaving
Signals, the core numbers, the holdings table, and Earnings. The
on-demand commands behind all three cut sections are unaffected.

### Requirements

- R1. The daily report no longer includes the Watchlist Support Levels
  table.
- R2. The daily report no longer includes the Extended Hours (pre/post-
  market) section.
- R3. The daily report no longer includes the AI Market Brief section.
- R4. `/watchlist`, `/support`, `/price`, and `/brief` remain fully
  functional and unchanged — all three cut sections' data stays reachable
  on demand.
- R5. This is a permanent code removal, not a settings toggle.

### Key Decisions

- **Hard-remove rather than a togglable setting** (session-settled:
  user-directed — chosen over a `/reportstyle`-style toggle: simpler, and
  the on-demand commands already cover the reversibility case). Governs
  R5.

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
