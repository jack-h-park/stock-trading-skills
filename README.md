# jackhpark-trading-agent

Skill-centric operating layer for **agentic brokerage trading**. Robinhood is the first
connected provider; the repo is designed so additional brokerages plug in as *providers*,
not as new repos.

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design, provider abstraction, data
  flow, control flow, safety model.
- [docs/operations.md](docs/operations.md) — runbook: schedule, hosting, install/move,
  verification, troubleshooting, placing a live trade.
- [docs/decisions.md](docs/decisions.md) — decision log with rationale for each major choice.

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
| `skills/reconcile/SKILL.md` | Drift check: Robinhood live vs manual Google Sheet | No |
| `skills/log/SKILL.md` | Append-only md log format contract | No |
| `config/holdings-sheet.md` | Sheet fileId, account mapping, drift thresholds | Robinhood map |
| `config/observatory-context.md` | Whole-portfolio reference context, and the limits on using it | No |
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

## Scheduled review (local launchd)

A read-only `portfolio-review` runs automatically on weekdays at **13:30 PT (16:30 ET, 30 min
after the US close)** via a macOS LaunchAgent, hosted on the always-on iMac
(`hermes-runner@imac-hermes`) so it isn't skipped when the laptop is asleep.

**Install / move the job to a host** (run on that machine, logged in as the owning user):

```bash
git clone git@github.com:jack-h-park/trading-agent.git ~/workspace/ai-assets/jackhpark-trading-agent
cd ~/workspace/ai-assets/jackhpark-trading-agent
# Claude must be logged into the SAME claude.ai account (so the Robinhood + Google Drive
# connectors are available). Verify, then install the LaunchAgent:
bash scripts/install-launchd.sh
# Test in the GUI session (Keychain/connectors only work there, not over plain ssh):
launchctl kickstart -k gui/$(id -u)/com.jackpark.trading-agent-review
```

> **Keychain note:** Claude stores its OAuth token in the macOS login Keychain, which is only
> accessible from the **GUI login session** (where launchd LaunchAgents run) — not from a
> non-interactive `ssh` shell. So `claude -p` works under launchd but a bare ssh test shows
> "Not logged in". Verify via `launchctl kickstart`, not ssh.

- Runner: [`scripts/run-review.sh`](scripts/run-review.sh) — invokes headless `claude -p`,
  which queries Robinhood **live** (no local data store), computes dip signals, writes a
  dated report to `logs/reviews/`, **reconciles Robinhood vs the Google Sheet** into
  `logs/reconcile/`, and commits.
- Schedule: [`scripts/com.jackpark.trading-agent-review.plist`](scripts/com.jackpark.trading-agent-review.plist)
  (tracked copy; the active copy lives in `~/Library/LaunchAgents/`).
- **Read-only by construction:** the runner whitelists only Robinhood *read* tools + file
  write + git via `--allowedTools`. `place_equity_order` / `cancel_equity_order` are not
  whitelisted, so the scheduled job physically cannot trade (verified).

Operate it:

```bash
# load / unload
launchctl load -w  ~/Library/LaunchAgents/com.jackpark.trading-agent-review.plist
launchctl unload   ~/Library/LaunchAgents/com.jackpark.trading-agent-review.plist
# run once now (bypasses the weekend guard)
FORCE=1 bash scripts/run-review.sh
# logs
cat logs/cron/$(date +%F).run.log
```

Data is never accumulated locally — Robinhood is the source of truth and is queried fresh
each run. `logs/reviews/` is a *decision journal* (the "why"), not a data store; `logs/cron/`
holds run logs and is git-ignored.

## Safety & responsibility

Order placement moves real money and is **irreversible**. The user is ultimately
responsible for every trade the agent places (per Robinhood's agentic-trading terms).
The default posture is **confirm before every live order** unless `config/guardrails.md`
grants explicit standing authorization for a narrow, well-defined case.
