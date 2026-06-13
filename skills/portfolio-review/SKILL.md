---
name: portfolio-review
description: >
  Run a periodic portfolio and market check for the trading-agent and write a dated markdown
  report. Use for daily/post-close reviews, on-demand "how's my portfolio" checks, or as the
  body of a scheduled cron job. Read-only by default — proposes, does not place, orders.
---

# Skill: portfolio-review

Read-only review. Produces a dated report in `logs/reviews/`. Does **not** place orders;
it may *propose* actions for the user to run through the `trade` skill.

## Steps

1. Read `strategy/policy.md` and `config/guardrails.md` for context.
2. Pull via the active provider (`providers/<broker>/adapter.md`):
   - `portfolio()` — equity, buying power, cash
   - `positions()` — current holdings, P/L
   - `quote()` / `historicals()` for universe symbols of interest
   - `orders()` — any open/unfilled orders
3. Assess against policy: position sizing vs. limits, universe drift, kill-switch condition,
   stale open orders.
4. **Write the report** to `logs/reviews/YYYY-MM-DD.md`:
   - Portfolio snapshot (value, cash, day change)
   - Positions table with P/L and % of portfolio
   - Flags: any guardrail breaches, kill-switch triggers, policy deviations
   - Proposed actions (explicit, each runnable via the `trade` skill) — **proposals only**
5. If a kill-switch condition is met, state it prominently at the top and recommend halting.

## Output contract

Append-only; one file per day. If today's file exists, append a new timestamped section
rather than overwriting. Same front matter as trade logs (see `skills/log/SKILL.md`).

## As a cron job

This skill is the intended body of a scheduled run (e.g. post-market-close). Keep it
read-only in automation; never auto-place from the scheduled path unless the user has
granted standing authorization in `config/guardrails.md`.
