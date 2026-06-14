# Decision log

Key choices and their rationale, newest-relevant first. Lightweight ADR style.

## 1. New dedicated repo, not the shared ai-skills library
A live, stateful trading agent (account config, logs, cron) is operational/project-specific.
The existing `jackhpark-ai-skills` repo is explicitly a *reusable, repo-agnostic* playbook
library and its README forbids project-specific commands/logs. Putting the agent there would
violate that charter. → Created `jackhpark-trading-agent` under `~/workspace/ai-assets/`
(home of the other `jackhpark-*` operating-asset repos; also an Obsidian vault for easy log
reading).

## 2. Broker-agnostic provider abstraction (not Robinhood-hardcoded)
Other brokerages may follow (Alpaca has an official MCP; Schwab/IBKR have APIs). To avoid a
new repo per broker, the operating logic depends on `providers/_contract.md`, and each broker
is an adapter. Cost of doing it now ≈ zero; cost of retrofitting later is high. Kept the
contract **thin** — it will firm up when a second provider actually lands.

## 3. Trade only the Agentic account
`get_accounts` shows only the Agentic account has `agentic_allowed: true`. The other Robinhood
accounts and external brokerages are reference/reconcile only. Pinned the account in the
adapter and policy so the agent can never trade elsewhere.

## 4. Rule-based swing, conviction-name universe (speculative names excluded)
The user's overall portfolio is aggressive (leveraged ETFs, quantum/space/meme names), but an
automated agent should trade only liquid, high-conviction names. Universe = 6 mega-caps the
user already holds + 2 broad ETFs. Leveraged ETFs and speculative small-caps are explicitly
prohibited for automation.

## 5. Notional **market** orders, regular hours only
Verified via `review_equity_order`: Robinhood notional ($-amount) and fractional orders are
accepted only as `type=market` in regular hours (limit orders need a whole-share quantity).
Every universe symbol trades > $100/share, so a $100 order *must* be a notional market order.
Acceptable only because the universe is ultra-liquid (~0.1% spreads → negligible slippage).
Consequence: the agent can only execute during regular US hours.

## 6. End-of-day signal → next-session execution (not intraday)
Standard daily-bar (EOD) systematic convention. Signals use the official **close** (most
reliable price; matches close-based backtests). Computing on the close and trading the same
close would be lookahead bias, so the honest, backtest-consistent rule is: signal on today's
close → execute next regular session. Tradeoff accepted: overnight gap risk. Alternative
(market-on-close, same-day) is more advanced and was deferred. Schedule = 13:30 PT = 30 min
after the 13:00 PT (16:00 ET) close so the close is settled.

## 7. Deterministic prioritization (option B)
When more symbols signal than the daily order cap allows, the choice of which to act on must
be reproducible for a rules-based money system (auditable, backtestable, trustworthy).
`strategy/policy.md` ranks by drawdown depth, tie-break alphabetical, top-N. Qualitative
context (conviction, news) goes only into the report's **Flags** as a note to the user — it
never reorders the proposal. The runner prompt enforces strict rule-following. Residual
nondeterminism (LLM arithmetic) is small; a future Python compute step (option C) would
remove it entirely at the cost of more code.

## 8. Scheduled job is structurally read-only
The cron must never trade unattended. `scripts/run-review.sh` whitelists only Robinhood read
tools + Drive read + file write + git via `--allowedTools`; order/cancel/sheet-write tools are
omitted. Verified a non-whitelisted order tool is denied headlessly without hanging
(`BLOCKED_AS_EXPECTED`). Live trades happen only in interactive sessions with confirmation.

## 9. No local data store
Robinhood is the source of truth and is queried live every run; the Google Sheet is read
live. The repo's `logs/reviews/` and `logs/reconcile/` are a **decision journal** (the "why",
which Robinhood doesn't keep), not a market-data cache. `logs/cron/` (run logs) is git-ignored.

## 10. Reconcile Robinhood vs the Google Sheet (read-only), don't auto-write
The user keeps the manual sheet as the cross-broker master and chose not to auto-update it.
The connected Google Drive MCP is read/create-only (no cell editing of an existing sheet)
anyway. So the agent **reports drift** (`skills/reconcile`) and suggests edits; the user
updates the sheet. Thresholds in `config/holdings-sheet.md` separate real drift (≥1 share or
≥1%, new/missing positions, qty-mismatch-with-same-avg = suspected typo) from DRIP/rounding
noise.

## 11. Other brokerages: read-only at best, via aggregators
Chase, Merrill, Fidelity have no retail trading API/MCP. Read-only access exists only through
aggregators (Akoya/Plaid; Fidelity is Akoya-gated) or Plaid-backed MCPs — credential-heavy
and coverage-flaky. Simplifi has no official API (CSV export + unofficial tools only) but
already aggregates all accounts. Decision: keep the manual Google Sheet + Drive MCP for read
context; revisit Plaid/Simplifi only if automation becomes worth the setup.

## 12. Host on the always-on iMac via launchd (local cron, not cloud)
User chose local cron. launchd on `hermes-runner@imac-hermes` (always on) so runs aren't
skipped when the laptop sleeps. Mirrors the existing `briefing-publish` migration pattern.

### 12a. macOS Keychain constraint (important)
Claude's OAuth token lives in the macOS **login Keychain**, reachable only from the **GUI
login session** (where launchd LaunchAgents run) — not from a non-interactive ssh shell. So a
bare `claude -p` over ssh shows "Not logged in" even though the job works under launchd.
Verification must use `launchctl kickstart` in the GUI domain, not ssh. The end-to-end FORCE
test in the GUI context returned `rc=0` with review + reconcile written and pushed.

### 12b. Host-portable runner + git pull
`run-review.sh` resolves its own repo path and finds `claude`/`git` via PATH (no hardcoded
username) so it runs on any Mac. It does `git pull --rebase --autostash` at start so a run on
one machine fast-forwards cleanly even if another machine or a manual commit pushed since.
