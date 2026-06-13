# jackhpark-trading-agent

Skill-centric operating layer for **agentic brokerage trading**. Robinhood is the first
connected provider; the repo is designed so additional brokerages plug in as *providers*,
not as new repos.

## What this repo is

- **Operating layer, not a code platform.** Trading execution happens through a connected
  brokerage MCP (Robinhood Agent MCP today). This repo holds the *operating logic*:
  strategy, guardrails, logging contract, periodic review, and the provider adapters that
  map a broker's tools onto a common contract.
- **Skill-first.** Behavior lives in `skills/*/SKILL.md`. Code is added only when a helper
  (log aggregation, etc.) genuinely earns it.
- **Markdown logs.** Every order and every periodic review is appended to `logs/` as md.

## Layout

| Path | Role | Broker-specific? |
|---|---|---|
| `strategy/policy.md` | Trading rules, universe, position limits, prohibitions | No |
| `config/guardrails.md` | Hard safety limits + confirmation policy | No |
| `skills/trade/SKILL.md` | Order flow: review → confirm → place → log | No |
| `skills/portfolio-review/SKILL.md` | Periodic portfolio/market check → md report | No |
| `skills/log/SKILL.md` | Append-only md log format contract | No |
| `providers/_contract.md` | Abstract interface every provider must satisfy | No |
| `providers/<broker>/adapter.md` | Maps broker tools ↔ the contract | **Yes** |
| `providers/<broker>/capabilities.md` | What the broker can/can't do | **Yes** |
| `logs/trades/YYYY-MM-DD.md` | Executed orders | broker column |
| `logs/reviews/YYYY-MM-DD.md` | Periodic review reports | broker column |

## Adding a new brokerage later

1. Create `providers/<broker>/adapter.md` mapping its tools onto `providers/_contract.md`.
2. Create `providers/<broker>/capabilities.md` for its constraints.
3. Strategy, guardrails, logging, and review skills are reused as-is. Add a `broker:` value
   to the log front matter. **No new repo.**

Keep the contract thin — refine it only when a second provider actually lands.

## Safety & responsibility

Order placement moves real money and is **irreversible**. The user is ultimately
responsible for every trade the agent places (per Robinhood's agentic-trading terms).
The default posture is **confirm before every live order** unless `config/guardrails.md`
grants explicit standing authorization for a narrow, well-defined case.
