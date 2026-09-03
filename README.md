# stock-trading-skills

A skill-first operating layer for **agentic brokerage trading**: the strategy, the
guardrails, the logging contract, and the provider adapters that map one broker's
tools onto a common interface. Robinhood is the first connected provider.

**This repo is not an agent.** It has no loop of its own and decides nothing on
its own — it is what an agent *reads*. Trading behaviour lives in
`skills/*/SKILL.md`, `strategy/policy.md` and `config/*.md` as prose an LLM
follows; the agency belongs to whatever runtime executes them.

It is also more than four skill files, and the name should not hide that. About
half of it is infrastructure: two MCP proxy servers, a broker token refresher, a
market-day guard, a headless runner, and the installers that schedule it. The
division is clean, though — **every trading decision is markdown, and all the
code is plumbing.**

> **Real money.** Order placement is irreversible and the account owner is
> responsible for every order placed, per the broker's agentic-trading terms.
> The default posture is confirm-before-every-order. Read
> [Safety](#safety) before running any of this.

## How this relates to the Hermes trader agent

These are two halves of one system, and the split is worth understanding before
you read anything else.

|  | `stock-trading-skills` (this repo) | Hermes `trader` profile |
|---|---|---|
| Answers | **what to trade, and under what limits** | **when to run it, and who gets told** |
| Contains | strategy, guardrails, skills, provider adapters, log contract | scheduler, Telegram delivery, the interactive session that can execute |
| Lives in | this repo, versioned | the operator's agent runtime, not here |
| Needs the other? | **No** — runs standalone under launchd or any scheduler | Yes — it schedules *this* repo's runner |

[Hermes](https://github.com/jack-h-park) is the author's own always-on agent
runtime: it owns the cron store, the notification channels, and the interactive
profile that a human talks to. It is **not required to use this repo.** Hermes
invokes `scripts/run-review.sh` as a plain script and delivers the digest; swap
it for launchd, systemd, or a CI schedule and nothing in `strategy/`,
`config/`, `skills/` or `providers/` changes.

The reason the split matters: **the automated path cannot trade, and the
interactive path can.** `scripts/run-review.sh` whitelists only read tools, so
the scheduled job physically cannot place an order. Execution happens only when
a human asks for it in an interactive session — which is where the operator's
agent runtime (Hermes here, whatever you use in your case) comes in.

## Using it yourself

This repo assumes you bring three things:

1. **An LLM agent that can read markdown instructions and call MCP tools.** The
   author runs Claude Code. The policy content ports anywhere — it is prose —
   but two things do not, and one of them matters a great deal:

   | Part | Ports to another runtime? |
   |---|---|
   | `strategy/`, `config/`, `providers/*.md` | Yes — runtime-agnostic prose |
   | `SKILL.md` layout | The content does; the convention is Claude Code's |
   | `scripts/run-review.sh` | No — it invokes `claude -p` directly |
   | **"the scheduled run cannot trade"** | **No — it comes from `--allowedTools`** |

   That last row is the one to read twice. The safety property this repo leans
   on is not a rule the agent is asked to obey; it is the absence of the
   order-placing tools from the runner's whitelist, which is a Claude Code
   feature. Port this elsewhere without an equivalent tool restriction and the
   guarantee is silently gone while the docs still claim it.
2. **A brokerage MCP server.** Robinhood's Agent MCP is what
   `providers/robinhood/` maps. Any broker with a tool surface can be added —
   see [Adding a brokerage](#adding-a-brokerage).
3. **Your own judgement about the strategy.** `strategy/policy.md` is one
   person's dip-buy rules on an eight-symbol universe with $100 notional orders.
   It is an example of the *shape* a policy takes here, not advice. Replace it.

### What you must change before running

| File | Why |
|---|---|
| `config/accounts.local.md` | Create it from `config/accounts.example.md`. Every doc refers to accounts by placeholder (`<AGENTIC_ACCOUNT>`); this file is where the real numbers live, and it is gitignored. |
| `strategy/policy.md` | The universe, entry and exit rules, and prioritisation. |
| `config/guardrails.md` | Per-order and per-day caps, the kill switch, and when — if ever — the agent may place without asking. |
| `config/holdings-sheet.md` | Only if you reconcile against a spreadsheet. Delete the reconcile skill if you don't. |

### Then

```bash
git clone git@github.com:jack-h-park/stock-trading-skills.git stock-trading-skills
cd stock-trading-skills
cp config/accounts.example.md config/accounts.local.md   # fill in, then chmod 600
FORCE=1 bash scripts/run-review.sh                        # one read-only run, now
```

A run queries the broker live, computes signals, reconciles if configured, and
writes a dated report under `logs/`. Nothing is committed and no order is
placed.

## Layout

| Path | Role | Broker-specific? |
|---|---|---|
| `strategy/policy.md` | What to trade: universe, entry/exit, prioritisation | No |
| `config/guardrails.md` | Hard limits + confirmation policy | No |
| `config/accounts.example.md` | Shape of the untracked file that resolves account placeholders | No |
| `config/trade-log-check.md` | Parameters for the unlogged-fill check | No |
| `config/holdings-sheet.md` | Sheet id, account mapping, drift thresholds | Sheet map |
| `config/observatory-context.md` | Whole-portfolio reference context, and the limits on using it | No |
| `skills/trade/SKILL.md` | Order flow: review → confirm → place → log | No |
| `skills/portfolio-review/SKILL.md` | Periodic signal scan → dated report (read-only) | No |
| `skills/reconcile/SKILL.md` | Broker vs spreadsheet drift check (read-only) | No |
| `skills/log/SKILL.md` | Append-only markdown log format contract | No |
| `providers/_contract.md` | The interface every provider must satisfy | No |
| `providers/<broker>/adapter.md` | Maps broker tools ↔ the contract | **Yes** |
| `providers/<broker>/capabilities.md` | What the broker can and cannot do | **Yes** |
| `scripts/run-review.sh` | Headless runner: review + reconcile + digest | No |

**`logs/` is not in this repo.** The journal names positions, quantities and
balances, so it stays on the machine that writes it. Only the format contract
(`skills/log/SKILL.md`) and the check parameters are tracked. A fresh clone
creates the directories on first run.

## Documentation

- [docs/architecture.md](docs/architecture.md) — system design, provider abstraction, data flow, control flow, safety model.
- [docs/operations.md](docs/operations.md) — runbook: schedule, hosting, install/move, verification, troubleshooting, placing a live trade.
- [docs/decisions.md](docs/decisions.md) — decision log with the rationale behind each major choice.

## Adding a brokerage

1. Write `providers/<broker>/adapter.md`, mapping its tools onto `providers/_contract.md`.
2. Write `providers/<broker>/capabilities.md` for its constraints — what it refuses,
   what it silently omits, where its data disagrees with itself.
3. Strategy, guardrails, logging and the review skills are reused as-is; add a
   `broker:` value to the log front matter. **No new repo.**

Keep the contract thin. Refine it when a second provider actually lands, not before.

## Scheduling

The runner is a plain script, so any scheduler works. Two are supported here:

**Standalone (launchd, macOS).** `bash scripts/install-launchd.sh` generates and
loads a LaunchAgent for weekdays at 13:30 PT — 30 minutes after the US close.
Use this if you are not running an agent runtime of your own.

**Hermes cron.** `scripts/hermes/install-cron.sh` declares the same job under the
author's `trader` profile, delivering the digest to Telegram. It is iMac-only and
refuses to run as any other user; it exists so the job's definition stays
versioned in this repo rather than in the runtime. The job is created **paused** —
installing is not enabling.

> **Keychain note (macOS).** Claude stores its OAuth token in the login Keychain,
> which is reachable from the **GUI login session** where LaunchAgents run — not
> from a plain `ssh` shell. `claude -p` therefore works under launchd while a bare
> ssh test reports "Not logged in". Verify with
> `launchctl kickstart -k gui/$(id -u)/<label>`, not over ssh.

```bash
FORCE=1 bash scripts/run-review.sh    # run once now, bypassing the market-day guard
cat logs/cron/$(date +%F).run.log     # what it did
```

## Safety

- **The scheduled job cannot trade.** `place_equity_order` and
  `cancel_equity_order` are absent from the runner's `--allowedTools` whitelist,
  so this is a structural property rather than an instruction the agent is asked
  to respect.
- **Every live order is confirmed** unless `config/guardrails.md` grants standing
  authorisation for a narrow, explicitly bounded case.
- **Nothing is accumulated locally.** The broker is the source of truth and is
  queried fresh on every run.
- **An unlogged fill is caught after the fact.** The interactive path is where the
  "review → confirm → place → log" contract can be skipped, so the daily review
  compares filled orders at the broker against `logs/trades/` and reports any
  fill with no entry. See `config/trade-log-check.md` — that check exists because
  eleven real orders once produced no journal entry at all.

None of this is investment advice, and none of it removes the account owner's
responsibility for what the agent does.

Maintained as the author's own trading setup, not as a supported project — expect
no response SLA on issues or PRs. Fork it and diverge rather than waiting.

## License

MIT — see [LICENSE](LICENSE).
