# Architecture

How the trading agent is put together and why. For day-to-day commands see
[operations.md](operations.md); for the rationale behind specific choices see
[decisions.md](decisions.md).

## What this system is

A **skill-centric, human-in-the-loop** agent that runs a rule-based swing strategy on a
single Robinhood "Agentic" account, plus a read-only reconciliation of Robinhood holdings
against a manual Google Sheet. Execution flows through MCP connectors; the repo holds the
*operating logic* (strategy, guardrails, logging, scheduling), not market data.

Two things happen each weekday after the US close, automatically and **read-only**:

1. **Signal review** — compute dip-buy signals for the universe, propose actions.
2. **Reconciliation** — diff Robinhood live positions vs the Google Sheet, flag drift.

Actual order placement is **never** automated — it happens only in an interactive session
with explicit user confirmation.

## Data sources (nothing is stored locally)

| Data | Source | Access |
|---|---|---|
| Accounts, portfolio, positions, orders | Robinhood | `claude_ai_Robinhood` MCP (live) |
| Quotes, historical OHLCV | Robinhood | `claude_ai_Robinhood` MCP (live) |
| Manual holdings master | Google Sheet | `claude_ai_Google_Drive` MCP (live, read-only) |

Robinhood is the source of truth for account state; the agent queries it fresh every run.
The repo's `logs/` is a **decision journal** (the "why"), not a data warehouse. See
[decisions.md#no-local-data-store](decisions.md).

## Provider abstraction

The system is broker-agnostic so a second brokerage (e.g. Alpaca) plugs in without a new
repo. Common skills call only the abstract operations in
[`providers/_contract.md`](../providers/_contract.md); a provider's `adapter.md` maps those
onto its concrete tools.

```
strategy/ + config/ + skills/      ← broker-agnostic (the operating logic)
        │ calls abstract ops
        ▼
providers/_contract.md             ← the interface every provider satisfies
        ▼
providers/robinhood/adapter.md     ← maps contract → claude_ai_Robinhood MCP tools
providers/robinhood/capabilities.md← what RH can/can't do (long equities/options only…)
```

Adding a broker = add `providers/<broker>/{adapter,capabilities}.md` and a `broker:` value
in logs. Strategy, guardrails, skills, and the cron are reused as-is.

## Repository layout

| Path | Role |
|---|---|
| `strategy/policy.md` | What to trade: universe, entry/exit, deterministic prioritization |
| `config/guardrails.md` | How much / whether to confirm: hard limits + confirm policy |
| `config/holdings-sheet.md` | Sheet fileId, sheet-section → RH account map, drift thresholds |
| `config/observatory-context.md` | Whole-portfolio reference from the Observatory's published summary, and the limits on using it |
| `skills/trade/SKILL.md` | Order flow: review → confirm → place → log |
| `skills/portfolio-review/SKILL.md` | Periodic signal scan → dated report (read-only) |
| `skills/reconcile/SKILL.md` | Robinhood vs Sheet drift check (read-only) |
| `skills/log/SKILL.md` | Append-only markdown log format contract |
| `providers/` | Contract + per-broker adapters/capabilities |
| `scripts/run-review.sh` | Host-portable headless runner (review + reconcile + commit) |
| `scripts/install-launchd.sh` | Generates + loads the LaunchAgent on the host machine |
| `logs/reviews/` , `logs/reconcile/` | Dated decision journal (tracked in git) |
| `logs/cron/` | Run logs (git-ignored) |

## The trading account

Trading is restricted to the Robinhood **Agentic** account `<AGENTIC_ACCOUNT>`
(`agentic_allowed: true`). The user's other Robinhood accounts (Long-term `<LONGTERM_ACCOUNT>`,
Mid-term `<MIDTERM_ACCOUNT>`) and external brokerages are **reference/reconcile only** — never traded.

| Account | agentic_allowed | Role |
|---|---|---|
| Agentic `••••0956` | ✅ | The only account the agent trades |
| Long-term `••••9965` | ❌ | Reconciled vs sheet; not traded |
| Mid-term `••••1478` | ❌ | Reconciled vs sheet; not traded |

## Strategy summary

- **Universe (8):** AAPL, MSFT, GOOGL, AMZN, NVDA, META, VOO, QQQM.
- **Entry:** $100 notional **market** buy when a symbol is ≥ 5% below its trailing 20-day
  high (notional orders must be market + regular hours — see decisions).
- **Exit:** +12% take-profit; −8% stop on single names (ETFs no stop). Sells always confirm.
- **Prioritization (deterministic):** when signals exceed the daily order cap, rank by
  drawdown depth, tie-break alphabetically. No qualitative reordering.
- **Guardrails:** $100/order, 3 orders/day, $1,000/day, 20% max position, −5% intraday
  kill switch, regular hours only.

## Control flow

```
[AUTO, weekday 13:30 PT]  launchd → scripts/run-review.sh → headless `claude -p`
    ├─ read strategy/, config/, providers/
    ├─ Robinhood MCP: portfolio, positions, quotes, historicals  (read-only)
    ├─ compute 20-day-high dip signals  →  logs/reviews/<date>.md
    ├─ Google Drive MCP: read sheet  →  diff vs RH  →  logs/reconcile/<date>.md
    └─ git add/commit/push
        ⚠ tool whitelist excludes place/cancel order + sheet write → cannot trade

[MANUAL, interactive session, market hours]  user: "execute the proposal"
    └─ recompute live → review_equity_order → user confirms → place_equity_order
       → log to logs/trades/<date>.md
```

## Safety model

- **Scheduled job is structurally read-only.** `scripts/run-review.sh` passes an
  `--allowedTools` whitelist of Robinhood *read* tools + Drive read + file write + git only.
  `place_equity_order` / `cancel_equity_order` and any Sheet-write are not whitelisted, so
  the cron physically cannot trade or mutate the sheet (verified: a non-whitelisted order
  tool returns `BLOCKED_AS_EXPECTED` without hanging).
- **Live trades require a human.** Confirm-before-place is the default in
  `config/guardrails.md`; standing auto-place is defined but off.
- **Order placement is irreversible** and the user is ultimately responsible for it
  (per Robinhood's agentic terms).
