---
name: portfolio-review
description: >
  Run a periodic portfolio and market check for the stock-trading-skills and write a dated markdown
  report. Use for daily/post-close reviews, on-demand "how's my portfolio" checks, or as the
  body of a scheduled cron job. Read-only by default — proposes, does not place, orders.
---

# Skill: portfolio-review

Read-only review. Produces a dated report in `logs/reviews/`. Does **not** place orders;
it may *propose* actions for the user to run through the `trade` skill.

## Scope

This skill covers **all of Jack's accounts**, not just the Agentic trading account:

| Account | Source | Trading scope |
|---|---|---|
| Robinhood Agentic (••••0956) | Robinhood MCP (live) | BUY/TRIM proposals + execution |
| Robinhood Long-term (••••9965) | Robinhood MCP (live) | Read-only — FYI flags only |
| Robinhood Mid-term (••••1478) | Robinhood MCP (live) | Read-only — FYI flags only |
| External brokerages (Chase, Fidelity, Merrill) | Google Sheet only | Read-only — FYI flags only |

All proposals and order execution remain scoped to the Agentic account. Flags for
non-Agentic accounts are informational — Jack acts on them outside this agent.

## Steps

1. Read `strategy/policy.md` and `config/guardrails.md` for context.
2. **Agentic account review** — pull via the active provider (`providers/<broker>/adapter.md`):
   - `portfolio()` — equity, buying power, cash
   - `positions()` — current holdings, P/L
   - `quote()` / `historicals()` for universe symbols of interest
   - `orders()` — any open/unfilled orders
3. Assess against policy: position sizing vs. limits, universe drift, kill-switch condition,
   stale open orders.
3b. Optionally read the Observatory summary per `config/observatory-context.md` — whole-portfolio
   reference for names this sleeve is about to touch. Reference only: it belongs in **Flags**
   as a note to the user and never changes a ranked list, vetoes a signal, or interacts with
   a guardrail. Its percentages have a different denominator from the guardrails' position
   cap. Missing is a normal state; note it in Flags and continue. Compute BUY signals and TRIM+REDISTRIBUTE signals per policy.
4. **Write the Agentic review** to `logs/reviews/YYYY-MM-DD.md`:
   - Portfolio snapshot (value, cash, day change)
   - Positions table with P/L and % of portfolio
   - Flags: any guardrail breaches, kill-switch triggers, policy deviations
   - BUY proposals and TRIM+REDISTRIBUTE proposals (explicit, each runnable via the `trade` skill) — **proposals only**
5. If a kill-switch condition is met, state it prominently at the top and recommend halting.
6. **Reconcile** — see `skills/reconcile/SKILL.md`. Reads Long-term and Mid-term positions
   from the Robinhood API and diffs against the Google Sheet.
7. **Full portfolio overview** — append to the same review file:
   - Read ALL account sections from the Google Sheet (Robinhood + external brokerages).
   - Get live quotes for all unique symbols across all accounts.
   - Compute current value and unrealized P/L % for every position.
   - Per-account value totals and cross-account grand total.
   - Highlights: positions with P/L ≤ −15% or P/L ≥ +50%, top 5 by value.
   - Label all non-Agentic flags as 'FYI — Jack to review manually'.

## Output contract

Append-only; one file per day. If today's file exists, append a new timestamped section
rather than overwriting. Same front matter as trade logs (see `skills/log/SKILL.md`).

## As a cron job

This skill is the intended body of a scheduled run (e.g. post-market-close). Keep it
read-only in automation; never auto-place from the scheduled path unless the user has
granted standing authorization in `config/guardrails.md`.
