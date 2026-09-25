---
title: US Market Holiday & Half-Day Scheduling - Plan
type: feat
date: 2026-09-25
topic: us-market-holiday-scheduling
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
product_contract_source: ce-brainstorm
execution: code
---

# US Market Holiday & Half-Day Scheduling

## Goal Capsule

- **Objective:** On a US market holiday or half-day, the bot's US-market
  jobs (open/close pings, reconciliation) reflect what actually happened
  that day instead of firing on their normal full-trading-day assumption.
- **Product authority:** The Product Contract below is authoritative.
  Planning should not re-litigate scope (US-only), which jobs skip vs.
  retime, or the calendar-source choice decided here.
- **Open blockers:** None.

## Product Contract

### Summary

The bot gains US market holiday and half-day awareness: on a full
holiday, US open/close pings and reconciliation are skipped entirely; on
a half-day, close-time jobs shift to the real early close instead of the
normal 4pm ET. The daily report is unaffected either way.

### Requirements

**Scope**

- R1. Holiday/half-day awareness applies to the US market only; SG
  scheduling is unchanged.

**Full holidays**

- R2. On a full US market holiday, the US market open ping, the US
  market close ping, and the US "10 min after close" reconciliation pass
  are all skipped entirely.
- R3. The fixed 20:00 SGT IBKR reconciliation catch-up pass is also
  skipped on a full US market holiday.

**Half-days**

- R4. On a US half-day, the US market open ping fires at its normal time
  — half-days don't affect market open.
- R5. On a US half-day, the US market close ping fires at the actual
  early close time for that day, not the normal 4pm ET.
- R6. On a US half-day, the "10 min after close" reconciliation pass
  fires 10 minutes after the actual early close time, not the normal
  close.

**Unaffected**

- R7. The daily report sends at its normal time regardless of US
  holidays or half-days.

**Reliability**

- R8. If the holiday/half-day calendar lookup fails or is unavailable,
  every job fails open — behaves as if it's a normal trading day —
  rather than silently skipping.

### Key Decisions

- **Scope narrowed to the US market**; SG holiday/half-day awareness is
  deferred (session-settled: user-directed — chosen over covering both
  markets in one plan: keeps one coherent work unit per artifact). Governs
  R1.
- **Full holidays skip pings and reconciliation entirely**, not just
  pings (session-settled: user-directed — nothing happened, nothing to
  check). Governs R2.
- **Half-days shift close-time jobs to the real early close** rather than
  leaving them at the normal time (session-settled: user-directed).
  Governs R5, R6.
- **The daily report is unaffected** by holidays or half-days
  (session-settled: user-directed — it's not tied to a single day's
  market activity). Governs R7.
- **The evening reconciliation catch-up pass also skips on full US
  holidays**, for consistency with the other US-market jobs
  (session-settled: user-directed). Governs R3.
- **Calendar source is a market-calendar library** (self-updating), not a
  hardcoded list requiring yearly manual maintenance (session-settled:
  user-directed).
- **Every job fails open on a calendar-lookup failure** — treats the day
  as a normal trading day rather than skipping — matching this bot's
  existing fail-open conventions elsewhere (e.g. IBKR reconciliation
  refusing to wipe holdings on a suspicious empty report rather than
  trusting a likely-broken fetch). Governs R8.

Key Flows are omitted: this is scheduling-timing behavior, not new
multi-step user-facing behavior — Requirements and Acceptance Examples
below fully describe what fires and when.

### Acceptance Examples

- AE1. **Covers R2.** Given Thanksgiving (a full US holiday), the US
  open ping, US close ping, and 10-min-after-close reconciliation do not
  fire that day.
- AE2. **Covers R3.** Given the same Thanksgiving holiday, the 20:00 SGT
  catch-up reconciliation also does not fire.
- AE3. **Covers R4, R5, R6.** Given the day after Thanksgiving (a
  half-day, early close 1pm ET), the open ping fires at the normal
  9:30am ET (+30s), the close ping fires at ~1pm ET instead of 4pm ET,
  and reconciliation's early pass fires ~1:10pm ET instead of ~4:10pm ET.
- AE4. **Covers R7.** Given either a full holiday or a half-day, the
  20:30 SGT daily report still sends normally.
- AE5. **Covers R8.** Given the calendar library fails to return data
  for a given date, every job behaves as though it's a normal trading
  day rather than skipping.

### Scope Boundaries

- Deferred for later: SG market holiday/half-day awareness — the same
  conversation raised it, but this plan covers US only.
- Deferred for later: surfacing action-needed items (triggered alerts,
  IBKR reconciliation changes) in the Signals section — a separate
  future area from the same conversation, not part of this plan.
- Deferred for later: multi-broker reconciliation support — a separate
  future area from the same conversation, not part of this plan.
- Out of scope: changing the daily report's own behavior on holidays or
  half-days (explicitly decided against — see Key Decisions).
