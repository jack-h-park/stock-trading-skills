#!/bin/bash
# Scheduled portfolio-review for the Robinhood Agentic account.
# Runs headless `claude -p` to: query Robinhood (live), compute dip signals,
# write a dated markdown review to logs/reviews/, and commit.
#
# PARALLEL FAN-OUT (2026-06-30): the review was a single sequential `claude -p`
# doing 10 steps in one agentic loop (~21 min on 2026-06-30, which overran the
# Hermes cron `script_timeout_seconds`). It is now decomposed into three
# INDEPENDENT read-only sub-jobs that run concurrently, each writing its own file
# (no shared-file or git races), followed by a small deterministic merge + a thin
# digest/commit step. Wall-clock drops from sum(A,B,C) to roughly max(A,B,C).
#   A — signals: Agentic BUY/TRIM proposals      -> logs/reviews/<date>.md
#   B — reconcile: live positions vs holdings sheet, AND filled Agentic orders
#                  vs logs/trades/ (the trade-log check) -> logs/reconcile/<date>.md
#   C — overview: full cross-account portfolio view -> logs/reviews/<date>.overview.md
#   merge+digest: assemble overview into the review, write the Telegram digest,
#                 and write the digests. It commits nothing: logs/ is gitignored.
#
# READ-ONLY BY DESIGN: the --allowedTools whitelist below grants only Robinhood
# *read* tools + file write + git. place_equity_order / cancel_equity_order are
# NOT whitelisted, so this scheduled job physically cannot trade. Only the final
# digest step is granted git; A/B/C cannot commit, which is what keeps them
# race-free when run in parallel.
#
# Invoked by the LaunchAgent installed via scripts/install-launchd.sh.
#
# Host-portable: resolves its own repo location and finds claude/git on any Mac
# (no hardcoded username/paths), so it runs on the MacBook or the always-on iMac.

set -uo pipefail

# Resolve repo from this script's location (scripts/ -> repo root).
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

# launchd runs with a minimal PATH; enrich it for common install locations.
for d in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin "$HOME/.hermes/node/bin" "$HOME/.npm-global/bin"; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin:/usr/sbin:/sbin"

# Load CLAUDE_CODE_OAUTH_TOKEN for headless cron (macOS Keychain is not accessible
# from launchd/cron; the token file is the durable workaround — see scripts/_env.sh).
[ -f "$HERE/_env.sh" ] && . "$HERE/_env.sh"

# _env.sh is sourced conditionally, so re-assert the pin here — `set -u` would
# otherwise abort the run if that file ever goes missing.
: "${TRADER_CLAUDE_MODEL:=claude-opus-4-8}"
: "${TRADER_DIGEST_MODEL:=claude-sonnet-4-6}"

# Same reason, one more variable. PROMPT_D interpolates $STOCK_DATA_DIR to tell
# the model where the journal archive lives; every other dollar sign in that
# prompt is escaped, so this one expands for real. Nothing sets it — not
# _env.sh, not the profile .env — so under `set -u` the PROMPT_D *assignment*
# aborted the whole script. On 2026-08-25 that killed the run between the merge
# and the digest step: jobs A/B/C had finished and written real exit proposals
# (an MSFT take-profit and an AAPL trim) to logs/reviews/, and none of it ever
# reached Jack, because the step that formats and delivers it never started. The
# run log ends mid-file with no error, since the abort also killed the logging.
: "${STOCK_DATA_DIR:=$HOME/workspace/data/stock-management}"

# A `claude -p` call that comes back 529 Overloaded has done no work and billed no
# tokens — it is a capacity answer, not a verdict on the request. One such answer
# used to end the phase for the day, so retry before giving up. Delays are indexed
# by attempt; running out of delays ends the loop even if MAX_ATTEMPTS is raised.
: "${CLAUDE_MAX_ATTEMPTS:=3}"
CLAUDE_RETRY_DELAYS=(20 60 150)

CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
GIT="$(command -v git || echo /usr/bin/git)"
PYTHON="$(command -v python3 || echo /usr/bin/python3)"

cd "$REPO" || exit 1

# Stay in sync with the remote so the post-run push fast-forwards cleanly even if
# another machine (or a manual commit) pushed since the last run.
"$GIT" pull --rebase --autostash --quiet 2>/dev/null || true

TODAY="$(date +%Y-%m-%d)"

# Resolve file paths the prompts hand to Claude HERE, in the shell. The Read tool takes a
# literal absolute path — it cannot expand $VARS or a leading ~ — so a prompt that names
# "$STOCK_BRIEFING_SUMMARY_PATH" or "~/workspace/..." leaves the agent no way to open the file
# except to shell out, and Bash is deliberately absent from the whitelists below. On
# 2026-08-18 that cost two denied Bash calls and the observatory context was silently skipped.
OBSERVATORY_SUMMARY="${STOCK_BRIEFING_SUMMARY_PATH:-$HOME/workspace/data/stock-management/outputs/stock-portfolio-observatory/briefing-summary.json}"
BRIEFING_JSON="$HOME/workspace/briefing/stock-portfolio-briefing/content/briefing-${TODAY}.json"
NOW="$(date '+%Y-%m-%d %H:%M:%S %Z')"
RUNLOG="$REPO/logs/cron/${TODAY}.run.log"
mkdir -p "$REPO/logs/cron" "$REPO/logs/digest" "$REPO/logs/reviews" "$REPO/logs/reconcile"

REVIEW_FILE="$REPO/logs/reviews/${TODAY}.md"
OVERVIEW_FILE="$REPO/logs/reviews/${TODAY}.overview.md"
RECONCILE_FILE="$REPO/logs/reconcile/${TODAY}.md"

# Phase 3 — append this run's `claude -p` token usage to the shared trader usage
# log, read by the hermes usage-collector → PM Observatory /usage (Trading system).
# Best-effort: any failure is swallowed so it can never break the review run.
_log_trader_usage() {
  local job="$1" resp="$2"
  local logfile="${TRADER_USAGE_LOG:-$HOME/.hermes/logs/trader-usage.jsonl}"
  mkdir -p "$(dirname "$logfile")" 2>/dev/null || true
  printf '%s' "$resp" | "$PYTHON" -c '
import json, os, sys, datetime
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
u = d.get("usage", {}) or {}
mu = d.get("modelUsage", {}) or {}
# Pick the model that did the work, not whichever key happens to come first:
# claude -p also bills a tiny auxiliary model (~20 output tokens) that sorts
# first, so next(iter(mu)) mislabelled every record.
model = max(mu, key=lambda k: (mu[k] or {}).get("outputTokens", 0), default="") if isinstance(mu, dict) and mu else ""
rec = {"date": datetime.date.today().isoformat(), "job": sys.argv[1], "model": model,
       "input_tokens": u.get("input_tokens", 0) or 0, "output_tokens": u.get("output_tokens", 0) or 0,
       "cache_read_tokens": u.get("cache_read_input_tokens", 0) or 0, "cost_usd": d.get("total_cost_usd", 0) or 0,
       "env": os.environ.get("TRADER_ENV", "prod")}
open(sys.argv[2], "a").write(json.dumps(rec) + "\n")
' "$job" "$logfile" 2>/dev/null || true
}

echo "===== run-review start $NOW =====" >> "$RUNLOG"

# Build a temporary --mcp-config JSON so claude -p can reach the Robinhood and
# Google Drive MCPs.  The Robinhood OAuth token is managed by Hermes; we read it
# (refreshing if needed) and inject it as a Bearer token for a remote SSE server.
# The Google Drive MCP is a local stdio Python script that uses a service-account key.
# All three parallel jobs share this one config (read-only tools only).
MCP_CONFIG_FILE="/tmp/trading-review-mcp-$$.json"
ROBINHOOD_TOKEN="$("$PYTHON" "$HERE/_get_robinhood_token.py" 2>>"$RUNLOG")"
if [ -z "$ROBINHOOD_TOKEN" ]; then
  echo "ERROR: could not obtain Robinhood OAuth token — aborting" >> "$RUNLOG"
  exit 1
fi
GCP_SA_KEY="$HOME/.config/stock-portfolio-briefing/gcp-sheets-sa.json"
SHEETS_MCP_SCRIPT="$REPO/scripts/mcp-google-sheets.py"

# The holdings sheet's fileId. accounts.local.md is gitignored (this repo is
# public), so it is the only place the real value exists; the MCP server used to
# carry the literal "<US_HOLDINGS_SHEET_ID>" placeholder and 404 on every call.
# The reconcile then reported "could not run" on four consecutive review days —
# 2026-08-24, 08-25, 08-28, 08-31 — while looking like a Google permissions
# problem. The other placeholders are resolved by the agent reading that file, as
# it instructs; a server process cannot do that, so it is passed in here.
#
# Missing is not fatal: the reconcile is one part of the review, and losing the
# whole run over it would be a worse trade. read_holdings names the fault instead.
ACCOUNTS_LOCAL="$REPO/config/accounts.local.md"
HOLDINGS_SHEET_ID="$(
  grep -F '<US_HOLDINGS_SHEET_ID>' "$ACCOUNTS_LOCAL" 2>/dev/null |
    awk -F'|' 'NF>=3 {gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3; exit}'
)"
if [ -z "$HOLDINGS_SHEET_ID" ]; then
  echo "WARNING: <US_HOLDINGS_SHEET_ID> not found in $ACCOUNTS_LOCAL — sheet reconcile will report it" >> "$RUNLOG"
fi
# The Google Sheets MCP requires the Hermes venv Python (has mcp + google-auth installed).
# Fall back to system python3 for local dev runs where the venv isn't present.
HERMES_VENV_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python"
[ -x "$HERMES_VENV_PYTHON" ] || HERMES_VENV_PYTHON="$PYTHON"
"$PYTHON" - "$MCP_CONFIG_FILE" "$ROBINHOOD_TOKEN" "$HERMES_VENV_PYTHON" "$SHEETS_MCP_SCRIPT" "$GCP_SA_KEY" "$HOLDINGS_SHEET_ID" << 'PYEOF'
import json, sys
out, token, py, script, sa, sheet_id = sys.argv[1:7]
cfg = {
    "mcpServers": {
        "robinhood": {
            "type": "http",
            "url": "https://agent.robinhood.com/mcp/trading",
            "headers": {"Authorization": f"Bearer {token}"}
        },
        "google-drive": {
            "command": py,
            "args": [script],
            "env": {"GOOGLE_APPLICATION_CREDENTIALS": sa, "HOLDINGS_SHEET_ID": sheet_id}
        }
    }
}
with open(out, "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF

# Per-job temp files for the three parallel agents' result JSON + stderr streams.
TMP_A_JSON="/tmp/trading-review-A-$$.json"; TMP_A_ERR="/tmp/trading-review-A-$$.err"
TMP_B_JSON="/tmp/trading-review-B-$$.json"; TMP_B_ERR="/tmp/trading-review-B-$$.err"
TMP_C_JSON="/tmp/trading-review-C-$$.json"; TMP_C_ERR="/tmp/trading-review-C-$$.err"
TMP_D_JSON="/tmp/trading-review-D-$$.json"; TMP_D_ERR="/tmp/trading-review-D-$$.err"
trap 'rm -f "$MCP_CONFIG_FILE" "$TMP_A_JSON" "$TMP_A_ERR" "$TMP_B_JSON" "$TMP_B_ERR" "$TMP_C_JSON" "$TMP_C_ERR" "$TMP_D_JSON" "$TMP_D_ERR"' EXIT

# Skip US market holidays / weekends cheaply: launchd already restricts to Mon-Fri,
# but skip if it's a weekend for any manual run. (Holiday skipping is left to the
# agent: with no fresh session the dip scan simply reflects the last close.)
DOW="$(date +%u)"  # 1=Mon .. 7=Sun
if [ "$DOW" -ge 6 ] && [ "${FORCE:-0}" != "1" ]; then
  echo "weekend ($DOW) — skipping (set FORCE=1 to run anyway)" >> "$RUNLOG"
  exit 0
fi

# Shared read-only toolset for the three parallel agents. NO git here — only the
# final digest step commits, so A/B/C cannot race on the index.
# NOTE: internal double-quotes inside the prompt strings must be escaped as \".
# Dollar signs passed literally to Claude must be escaped as \$.
READ_ALLOWED=(
  "mcp__robinhood__get_accounts"
  "mcp__robinhood__get_portfolio"
  "mcp__robinhood__get_equity_positions"
  "mcp__robinhood__get_equity_quotes"
  "mcp__robinhood__get_equity_historicals"
  "mcp__robinhood__get_equity_orders"
  "mcp__robinhood__get_equity_tradability"
  "mcp__google-drive__read_holdings"
  "mcp__google-drive__read_sheet"
  "Read" "Write" "Edit" "Grep" "Glob"
)
DIGEST_ALLOWED=(
  "Read" "Write" "Edit" "Grep" "Glob"
  "Bash(git status:*)"
)

CRITICAL_RO="CRITICAL: This is READ-ONLY. Do NOT place or cancel any orders under any circumstances. Do not call place_equity_order or cancel_equity_order. Do not write to the Google Sheet. Do NOT run any git command — committing is handled by a separate step."

# ── Job A — Agentic BUY/TRIM signals + review ────────────────────────────────
PROMPT_A="You are running the scheduled, READ-ONLY portfolio-review for this repo's Robinhood Agentic trading account. Today is ${TODAY}. Produce ONLY the Agentic signals report — reconcile and the cross-account overview are handled by other jobs; do not do them here.

Steps:
1. Read strategy/policy.md, config/guardrails.md, config/trim-policy.md, providers/robinhood/adapter.md, providers/robinhood/capabilities.md, and skills/portfolio-review/SKILL.md + skills/log/SKILL.md.
2. Using the Robinhood MCP (tools are prefixed mcp__robinhood__), for the Agentic account in the adapter: get_portfolio and get_equity_positions; get_equity_quotes and get_equity_historicals (interval=day, last ~30 days) for the universe symbols in policy.md.
3. BUY SIGNALS — Compute each universe symbol's trailing 20-trading-day high and its drawdown vs the latest price. Flag a BUY signal when price is >= 5% below the 20-day high, per policy.md entry rules.
3b. TRIM SIGNALS — For each symbol with an open position in the Agentic account, check the trim conditions from config/trim-policy.md:
    a. Skip if: position market value < minimum, kill-switch active, position was opened < 3 calendar days ago, or a stop-loss/take-profit signal already applies (use the stronger exit).
    b. Compute the 20-day high (same historicals data from step 2). If current price ≤ (20-day high × (1 − threshold)), flag a TRIM signal.
    c. For each TRIM signal: compute shares to sell (50% of held shares, rounded DOWN to 6 decimal places — fractional is allowed and there is NO whole-share minimum; every universe symbol trades above \$100 so a whole-share rule would round every real position to zero), estimated proceeds (shares × current price), and which currently-open positions qualify as REDISTRIBUTE recipients (return-since-purchase > portfolio-average return, symbol in universe).
    d. Compute REDISTRIBUTE allocation: split proceeds equally among recipients, rounded to whole dollars; if no recipients, mark proceeds as 'held as cash'; if any allocation < minimum, drop the lowest outperformer and recompute.
    e. Show all numbers explicitly so the proposal is fully auditable: symbol, 20-day high, current price, drawdown %, shares to sell, estimated proceeds, redistribute-to list with dollar amounts.
3c. EXIT SIGNALS (take-profit, stop-loss) — these are measured against AVERAGE COST, not the 20-day high, and until now nothing here told you how to size them, which is why a past review proposed selling 'up to' a quantity. For every open Agentic position compare the current price with its average cost:
    a. TAKE-PROFIT at >= +12%: propose selling 50% of shares held, rounded DOWN to 6 decimal places — the same two parameters TRIM uses, in config/trim-policy.md. SKIP the signal when the position's market value is below that table's minimum. That floor is not optional: selling never changes average cost, so a position at +15% is still at +15% after a half-sale and would qualify again every session; the floor is what ends the sequence after one trim.
    b. STOP-LOSS at <= -8%, single names only (the ETFs in policy.md have no hard stop): propose closing the WHOLE position.
    c. Never stack exits on one symbol in one review. Stop-loss overrides everything. A take-profit and a TRIM are both 50%, so they do not add up — take one of them, once.
    d. State the exact share count and estimated proceeds. Never write 'up to' or any other hedge: the policy fixes the quantity, so the proposal must too.
3d. COMMITTED ADD CAPITAL — compute it per symbol before ranking anything, because the averaging-down rule in strategy/policy.md is stated as a running figure and not as a count. Read this account's full fill history for the symbol with mcp__robinhood__get_equity_orders, oldest first, and replay it: a buy adds its dollar amount, a sale multiplies the running figure by (1 − shares_sold / shares_held_before_the_sale) and the result is ROUNDED TO CENTS, a full exit zeroes it. The rounding is not cosmetic: fractional share counts do not divide evenly, and carried at full precision a 50% take-profit leaves \$100.00011766 committed and refuses the next \$100 add by a hundredth of a cent. State the result for every symbol you are about to rank or block, as 'committed \$X of \$200' — a blocked symbol must show the number that blocked it, the same way a drawdown is shown. If the fill history does not reach back to the position's opening the figure cannot be trusted: say so in Flags and treat the symbol as at the cap, which is the safe direction.
4. Apply guardrails to BUY signals only (5 orders/day AND 6 orders/week max, \$100 each, 20% position cap — the table in config/guardrails.md is the source of truth if these ever disagree). The week runs Monday-Sunday; count this week's already-filled buys (including any REDISTRIBUTE buys) before ranking, and state the remaining weekly slots in the report. Build the prioritized BUY list per the 'Prioritization (deterministic)' section of policy.md. TRIM signals are not subject to the buy order cap — they are exits. Show the drawdown number for each ranked BUY. Qualitative context may change the ranked list, but ONLY as a declared departure — see strategy/policy.md, \"Departing from a threshold\". Every threshold in policy.md is a default you may read past when the situation says otherwise; every limit in config/guardrails.md is not, and no reasoning moves it. If you depart, name the threshold, its number, what you did instead, and why, in one line each, and record it in the departures array of the decision record. If you do not depart, change nothing on qualitative grounds and put the context in Flags as before — a departure is a considered exception, not a licence to re-rank on narrative.
4b. OBSERVATORY CONTEXT — read config/observatory-context.md and follow it. In short: use the Read tool on ${OBSERVATORY_SUMMARY} (already resolved for you — do not shell out, and do not try to expand any variable or ~ yourself; Bash is not available to this job). If Read reports the file does not exist, that is the missing case below, not an error to work around; refuse it if schemaVersion > 1; keep ONLY items concerning a universe symbol or a symbol with an open Agentic position, at most 3; write them into the report's 'Flags' section as notes to Jack. If the file is missing, unreadable, or refused, write one line in Flags saying so and carry on — that is a normal state, not a failure. CRITICAL: these are whole-portfolio figures across every account and brokerage, denominated in KRW, and they already include the Agentic sleeve. Their percentages have a different denominator from the 20% Agentic position cap in config/guardrails.md — never compare them to it, and never let one satisfy or trigger a guardrail. This context must NOT change the ranked BUY list or its order, must NOT suppress or veto a signal, and must NOT be turned into a do-not-add rule.
4b2. BUY-vs-EXIT CONFLICTS — a symbol carrying BOTH a BUY signal and any exit signal (TRIM, take-profit, stop-loss) is not yours to resolve. The thresholds overlap by construction (entry at >=5% below the 20-day high, TRIM at >=10%), so this is expected, not an anomaly. Read config/rulings.json first: if the key \"<SYMBOL>:buy-vs-exit\" is already answered there, apply that ruling silently and say in the report that you applied a standing ruling, quoting its date. If it is NOT answered, do NOT raise a decision request for a threshold overlap — read the situation, act on one side, and record the other as a departure per strategy/policy.md, \"Departing from a threshold\"; mark the item autoEligible false either way. Reserve decision requests for situations the rules cannot anticipate and you should not decide alone — a pending acquisition, a halt, an earnings gap that makes the 20-day high meaningless. Never pick a side yourself and never let a guardrail that happened to block one side stand in for a decision: on 2026-08-31 the committed-add cap blocked the GOOGL buy and the conflict went unnamed. If the SAME conflict kind has been ruled the same way for three or more DIFFERENT symbols in config/rulings.json, say so and propose the wording for a policy rule instead of asking again — see strategy/policy.md, \"When a ruling should stop being a ruling\".
4c. DECISION RECORD — also write logs/reviews/${TODAY}.decisions.json, the same conclusions as machine-readable JSON. This is not a second analysis: emit the numbers you already computed above, or the file is worthless. Exact shape, no extra keys:
{\"date\": \"${TODAY}\", \"targetSession\": \"<the next regular session these would execute in, YYYY-MM-DD>\", \"priceAsOf\": \"<the date of the closing prices you used, YYYY-MM-DD>\", \"decisions\": [{\"side\": \"buy|sell\", \"symbol\": \"SYM\", \"kind\": \"buy|trim|take-profit|stop-loss\", \"shares\": <number or null for notional buys>, \"notional\": <number>, \"priceUsed\": <number>, \"autoEligible\": <true|false>, \"reason\": \"<short>\"}], \"blocked\": [{\"symbol\": \"SYM\", \"rule\": \"<the rule that blocked it>\", \"value\": \"<the number behind it>\"}], \"departures\": [{\"symbol\": \"SYM\", \"threshold\": \"<which one, e.g. TRIM >=10% below 20-day high>\", \"thresholdValue\": \"<the number>\", \"observed\": \"<what the measurement actually was>\", \"did\": \"<what you did instead>\", \"why\": \"<one line>\"}], \"decisionRequests\": [{\"key\": \"<SYMBOL>:buy-vs-exit\", \"symbol\": \"SYM\", \"question\": \"<the choice, in one line>\", \"readings\": [{\"side\": \"buy|exit\", \"detail\": \"<the rule and its number>\"}]}]}
Set autoEligible true ONLY when every condition in the 'Standing authorization' list in config/guardrails.md holds for that order AND the order carries no departure — a departure is judgement rather than arithmetic, so it goes to Jack to read — in particular it is false for every sell, and false for a buy whose symbol also carries an exit signal. You are not placing anything; this file records what the rules determined so it can be checked afterwards against what actually happened.
5. Write the report to logs/reviews/${TODAY}.md following the format in skills/log/SKILL.md (append a timestamped section if the file already exists). Include both BUY proposals and TRIM+REDISTRIBUTE proposals in separate sections. PROPOSALS ONLY. Do not add a portfolio-overview section here. Do not commit.

${CRITICAL_RO}"

# ── Job B — Reconcile live positions vs the holdings sheet ────────────────────
PROMPT_B="You are running the scheduled, READ-ONLY reconcile step for Jack's brokerage accounts. Today is ${TODAY}. Produce ONLY the reconcile drift report.

Steps:
1. Read skills/reconcile/SKILL.md, config/holdings-sheet.md and config/trade-log-check.md.
2. SHEET DRIFT: using the Robinhood MCP get_equity_positions for the Long-term and Mid-term accounts in the mapping, and the mcp__google-drive__read_holdings tool (reads the full holdings sheet automatically; use mcp__google-drive__read_sheet if you need a custom range), diff Robinhood live positions against the sheet per the thresholds in the skill.
2b. TRADE-LOG CHECK: call mcp__robinhood__get_equity_orders for the Agentic account <AGENTIC_ACCOUNT> with state=filled and placed_agent=agentic. Keep only fills whose date is within the lookback window AND on or after the floor date in config/trade-log-check.md — silently discard everything before the floor, which is the documented pre-existing backlog in the untracked logs/trades/README.md and is not a finding. For each remaining fill, Glob/Read logs/trades/<fill-date>.md and decide whether it covers that fill: the entry names the order id, or failing that the same symbol and side on that date. Any fill with no covering entry is a finding, and so is any fill you cannot decide — the config fixes the tie-break toward reporting. This is bookkeeping about THIS repo's journal, not a position discrepancy and not an unauthorised trade: the broker is right and the log is incomplete. Do NOT write, create or amend anything under logs/trades/ — reporting the gap is the whole job, and writing an entry from broker data would fabricate a decision record (docs/decisions.md#15).
3. Write both results to logs/reconcile/${TODAY}.md, the trade-log findings under their own '## Trade-log check' heading per the alert wording in config/trade-log-check.md. Write that heading even when the window is clean. Where you note that the Agentic account was not sheet-reconciled, say which reconcile it is out of scope for and that the trade-log check covers it — do not write the bare sentence 'Agentic account (<AGENTIC_ACCOUNT>) is out of scope for this reconcile', which reads as though the one account this repo trades is the one account nothing checks. Report only — do NOT write to the sheet. Do not commit.

${CRITICAL_RO}"

# ── Job C — Full cross-account portfolio overview (informational) ─────────────
PROMPT_C="You are running the scheduled, READ-ONLY full-portfolio overview for Jack's accounts. Today is ${TODAY}. This is INFORMATIONAL ONLY — no proposals, no orders. Produce ONLY the overview.

Steps:
1. Read config/holdings-sheet.md and strategy/policy.md (for the account mapping and universe).
2. FULL PORTFOLIO OVERVIEW: Build a comprehensive view across ALL of Jack's accounts.
   a. Read the holdings sheet via mcp__google-drive__read_holdings. Extract every account section: Robinhood Long-term, Robinhood Mid-term, and any external brokerage sections present (Chase, Fidelity, Merrill). External brokerage data reflects the sheet only — no API available.
   b. For Robinhood Long-term (••••9965) and Mid-term (••••1478): use live mcp__robinhood__get_equity_positions for quantity and avg_buy_price — these are more accurate than the sheet.
   c. Compile a deduplicated list of ALL unique symbols across every account.
   d. Call mcp__robinhood__get_equity_quotes for all unique symbols in one or more batches to get current prices.
   e. For each position compute: current_value = qty × current_price; unrealized_pl_pct = (current_price − avg_cost) / avg_cost × 100. Use API avg_buy_price for Robinhood accounts; use sheet avg_cost for external accounts.
   e2. CASH BALANCES — for EACH Robinhood account (Agentic ••••0956, Long-term ••••9965, Mid-term ••••1478), call mcp__robinhood__get_portfolio to read the live uninvested cash / buying-power balance and include it in that account's total. Do NOT report equity-only totals. For external brokerage accounts, include a cash row only if the sheet shows one (no API available).
   f. Per-account totals: sum current_value for all positions in each account PLUS that account's cash balance from step e2 (Robinhood) or the sheet cash row (external). Show the cash component explicitly in the per-account line so the total is auditable.
   g. Cross-account grand total: sum all per-account totals (equity + cash).
   h. Highlights — flag these regardless of account (label as 'FYI — Jack to review manually'):
      - Any position with unrealized P/L ≤ −15% (significant drawdown from cost basis).
      - Any position with unrealized P/L ≥ +50% (substantial gain; potential rebalance candidate).
      - Top 5 positions by current value across all accounts.
3. Write a SELF-CONTAINED markdown section to logs/reviews/${TODAY}.overview.md (overwrite if it exists) titled with a '## Full Portfolio Overview' heading, containing: (1) account-by-account summary table (account | # positions | total value), (2) a clearly labelled cross-account grand total line, (3) highlights table.
3a. THE GRAND TOTAL ALWAYS MEANS THE SAME THING: every asset every account holds — equities, cash AND crypto — with nothing excluded. State the composition on the total line, naming each non-equity component and its amount (e.g. 'includes \$1,982.97 cash and \$19,908.20 crypto in Mid-term'). This is a day-over-day figure Jack tracks, so a total whose definition moves is worse than one that is merely large: on 2026-07-30 crypto was excluded and said so, on 07-31 it was silently included, and the number jumped \$22,642 on a day the US market rose about 1%. If a component cannot be priced, still list it and say so rather than dropping it from the sum. This section is informational only — all proposals and execution remain scoped to the Agentic account. Do not write to logs/reviews/${TODAY}.md. Do not commit.

${CRITICAL_RO}"

# ── Run A, B, C concurrently; each writes its own file, none commit. ──────────

# Retry only what a second call could plausibly answer differently: an upstream
# capacity/transient status, or a CLI that died without producing any JSON. A run
# that answered cleanly is never retried — its file is already written and a
# second pass would append a duplicate section.
_is_retryable() { # $1=rc  $2=jsonfile
  [ ! -s "$2" ] && return 0
  grep -q '"api_error_status":\(429\|500\|502\|503\|529\)' "$2" && return 0
  return 1
}

_run_with_retry() { # $1=prompt $2=jsonfile $3=errfile $4=label $5=digest|read
  local attempt=1 rc delay
  while : ; do
    if [ "$5" = "digest" ]; then
      "$CLAUDE" -p "$1" \
        --model "$TRADER_DIGEST_MODEL" \
        --output-format json \
        --allowedTools "${DIGEST_ALLOWED[@]}" \
        > "$2" 2> "$3"
    else
      "$CLAUDE" -p "$1" \
        --model "$TRADER_CLAUDE_MODEL" \
        --output-format json \
        --mcp-config "$MCP_CONFIG_FILE" \
        --allowedTools "${READ_ALLOWED[@]}" \
        > "$2" 2> "$3"
    fi
    rc=$?
    [ "$rc" -eq 0 ] && return 0
    _is_retryable "$rc" "$2" || return "$rc"
    delay="${CLAUDE_RETRY_DELAYS[$((attempt - 1))]:-}"
    [ "$attempt" -ge "$CLAUDE_MAX_ATTEMPTS" ] && return "$rc"
    [ -z "$delay" ] && return "$rc"
    echo "----- job $4 attempt $attempt failed (rc=$rc), retrying in ${delay}s -----" >> "$RUNLOG"
    sleep "$delay"
    attempt=$((attempt + 1))
  done
}

run_job() { # $1=prompt  $2=jsonfile  $3=errfile  $4=label
  _run_with_retry "$1" "$2" "$3" "$4" read
}

# ── Shadow check: yesterday's decision record against what actually settled. ──
# Runs BEFORE this session's review, not after it, and that ordering is the whole
# trick. A record written at 13:30 names prices for a session whose settled closes
# are not in the observatory database yet — the refresh that stores them runs later
# — so checking it on the same day can only ever say "no settled row". By the next
# review the closes have landed and the comparison is real.
#
# Reports; never gates. See docs/decisions.md#16.
SHADOW_OUT="$REPO/logs/shadow/latest.txt"
mkdir -p "$(dirname "$SHADOW_OUT")"
"$PYTHON" "$HERE/shadow-check.py" --repo "$REPO" --quiet > "$SHADOW_OUT" 2>>"$RUNLOG" || true
if [ -s "$SHADOW_OUT" ]; then
  echo "----- shadow-check findings -----" >> "$RUNLOG"; cat "$SHADOW_OUT" >> "$RUNLOG"
fi

echo "----- launching parallel jobs A/B/C $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"
run_job "$PROMPT_A" "$TMP_A_JSON" "$TMP_A_ERR" A & PID_A=$!
run_job "$PROMPT_B" "$TMP_B_JSON" "$TMP_B_ERR" B & PID_B=$!
run_job "$PROMPT_C" "$TMP_C_JSON" "$TMP_C_ERR" C & PID_C=$!
wait "$PID_A"; RC_A=$?
wait "$PID_B"; RC_B=$?
wait "$PID_C"; RC_C=$?
echo "----- parallel jobs done A=$RC_A B=$RC_B C=$RC_C $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"

# Fold each job's stderr + result JSON into the run log, and record token usage.
for pair in "A:$TMP_A_JSON:$TMP_A_ERR" "B:$TMP_B_JSON:$TMP_B_ERR" "C:$TMP_C_JSON:$TMP_C_ERR"; do
  lbl="${pair%%:*}"; rest="${pair#*:}"; jf="${rest%%:*}"; ef="${rest#*:}"
  echo "----- job $lbl stderr -----" >> "$RUNLOG"; cat "$ef" >> "$RUNLOG" 2>/dev/null
  echo "----- job $lbl result json -----" >> "$RUNLOG"; cat "$jf" >> "$RUNLOG" 2>/dev/null
  # Log under the stable "trading-review" job name (not per-phase) so the
  # Observatory /usage daily total still rolls up across all sub-calls.
  _log_trader_usage "trading-review" "$(cat "$jf" 2>/dev/null)"
done
echo "----- usage logged, entering merge $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"

# ── Deterministic merge: fold the overview fragment into the review file. ─────
# C owns a separate fragment file precisely so it never races A on the review
# file; here (single-threaded) we splice it in and drop the fragment.
if [ -f "$OVERVIEW_FILE" ]; then
  if [ -f "$REVIEW_FILE" ]; then
    { echo; echo; cat "$OVERVIEW_FILE"; } >> "$REVIEW_FILE"
  else
    cp "$OVERVIEW_FILE" "$REVIEW_FILE"
  fi
  rm -f "$OVERVIEW_FILE"
fi
# 2026-08-25: the run ended here with no trace and no logs/cron/<date>.status —
# A/B/C had all completed (rc=0 each, logged above), so whatever happened next
# left no line in this file at all. This marker exists so a repeat narrows to
# "died merging" vs "died building/running the digest prompt" instead of the
# whole A/B/C-to-D gap; if it recurs, the delta between this line and the next
# one is where to look.
echo "----- merge done, building digest prompt $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"

# ── Digest + single commit. Thin step: reads the assembled local files only. ──
PROMPT_D="You are writing the notification digest and committing the scheduled portfolio review for ${TODAY}. The heavy work is already done and written to local files. Do NOT call any Robinhood or Google tools.

Steps:
1. Read logs/reviews/${TODAY}.md (contains the Agentic BUY/TRIM proposals and a 'Full Portfolio Overview' section) and logs/reconcile/${TODAY}.md (the reconcile drift report). If either file is missing, note it but continue with what exists.
2. BRIEFING CONTEXT: use the Read tool on ${BRIEFING_JSON} (already resolved — do not shell out or expand ~ yourself). Extract the macro, moverNotes, and actions fields. Keep at most 2 observations directly relevant to the universe symbols or current open positions. If the file does not exist, skip silently.
2b. ACT-NOW OVERRIDE: each action carries a \"priority\" of act-now, this-week, or fyi. An action whose priority is \"act-now\" is ALWAYS carried into the digest, in addition to the 2 relevance-picked observations and regardless of whether its symbol is in the Agentic universe — the briefing allows at most one per day and zero is the normal case, so it is the one item the writer marked as decidable today. Lead the briefing note with it, and say plainly if it concerns a holding outside the Agentic account so Jack knows it is not actionable by this sleeve. CARRY ITS DEADLINE ONLY IF THAT DEADLINE IS STILL IN THE FUTURE WHEN YOU WRITE THIS. The briefing publishes at 08:00 PT, mid-session; this review runs post-close, so an act-now deadline the briefing wrote as \"before the close\", \"before the bell\" or \"today\" has already expired by the time you repeat it, and repeating it verbatim asks Jack to do something he can no longer do. On 2026-09-01 the PANW item went out at 13:41 PT saying \"decide before the bell\" — 41 minutes after the 13:00 bell, on a session the stock fell 5.2% during. Check the deadline against the current time and rewrite it: if the window is still open say so plainly, if the next chance is the following session say that, and if the catalyst has already happened say THAT and drop the instruction entirely rather than dressing a closed decision as an open one. This is the same arithmetic the slots line below already does — a deadline nobody can meet is the sibling of a budget nobody can spend. Never restate an act-now as a proposal or a guardrail input: it is context for Jack, and it must not change the ranked BUY list.
3. TWO DIGESTS, split by what Jack can act on. They go to different channels, so nothing may appear in both — a fact belongs to exactly one file. Conversational tone in each; warm but concise, no markdown tables, no robotic headers. Neither file is committed.

3a. ACTIONABLE — write logs/digest/${TODAY}.agentic.md (overwrite if it exists), <= 1600 characters. ONLY the Robinhood Agentic account (<AGENTIC_ACCOUNT>), the one account whose proposals Jack can execute. Nothing from any other account belongs here.
   - Opening line: start with '**Hermes Trader — Agentic (••••0956)**' so the message names its own author. It is delivered by a Discord bot shared with another agent and therefore appears under that agent's display name; until the trader has its own bot, the first line is what tells Jack whose proposals these are.
   - Then the sleeve's own total value and its day change, and the order slots for THE SESSION THESE PROPOSALS WOULD EXECUTE IN — not the one that just closed. This review always runs post-close, so every proposal targets the next regular session: on Mon-Thu that is tomorrow, on Friday it is Monday and a fresh week. Write it as 'slots for <weekday>: N of 5 today, M of 6 that week' using the caps in config/guardrails.md and counting fills Mon-Sun. Saying '5 left today' about a session that has already closed states a budget nobody can spend.
   - Discord renders markdown natively, so put **bold** on the sleeve value, each proposed symbol, and each exit's share count — the numbers Jack acts on. Do not bold whole sentences.
   - Every ranked BUY proposal as '\$100 <SYM> (<drawdown>%)', and name any symbol that signalled but was blocked, with the rule that blocked it AND the number behind it — 'AMZN: committed \$200 of \$200' rather than 'at the add cap'. A blocked line without its figure is the reader taking your word for it, and this is the rule that decides where money goes.
   - Every TRIM proposal as 'TRIM <SYM> <shares>sh (~\$<proceeds>) — <drawdown>% below 20d high' followed by 'REDISTRIBUTE → <SYM> \$<amount>, <SYM> \$<amount>' (or 'proceeds held as cash' if no recipients). If there are no TRIM signals, say so explicitly.
   - Any take-profit or stop-loss exit, with the share count and estimated proceeds.
   - UNLOGGED TRADES, when and only when the reconcile step's '## Trade-log check' section reported findings. One short section headed 'Unlogged trades:' naming each fill (date, symbol, side, shares, price) and saying plainly that the fix is to write the missing entry in logs/trades/ per skills/log/SKILL.md, reconstructing why that order was placed — not to place or cancel anything. It belongs in this message because it is a thing Jack has to DO, and it decays: the reasoning is recoverable from the session it happened in and stops being recoverable soon after. When the check is clean, write nothing about it here — the one-line clean state goes in the reference message.
   - RECONCILE, when and only when the reconcile step found sheet drift. One short section headed 'Reconcile:' naming each alert, the account, and what to change in the holdings sheet. It sits here rather than in the reference message because it is the one other thing in this review that asks Jack to DO something, and that is what decides which channel a line goes to — not which account it concerns. Say plainly that fixing it means editing the sheet, not placing an order. When the sheet matches, write nothing about reconcile at all: 'no drift' is the expected state and belongs in the read-only message.
   - DEPARTURES, when and only when you departed from a threshold. Its own short section headed 'Read past a threshold:' naming the threshold and its number, what you did instead, and why, per item. Put it above the proposals it affects. Jack is reading the reasoning here, not checking arithmetic, so give him the one sentence that would let him disagree — and say plainly that a departed item is not auto-eligible and needs his confirmation either way.
   - DECISION REQUESTED, when and only when the review raised one. Its own section headed 'Decision needed:' naming the symbol, both readings with their numbers, and that nothing on that symbol executes either way until Jack rules. Lead with this above the proposals: it is the only item in the message where his answer carries information the rules do not already have — everything else is arithmetic he is being asked to countersign. Say that the answer is recorded and will not be asked again.
   - SHADOW CHECK, when and only when logs/shadow/latest.txt is non-empty. One short section headed 'Shadow check:' carrying what it says. That file is only written when a decision this review made yesterday disagrees with what the market settled at, which means the price a rule acted on was not the price — the fault class that confirm-before-place hides because a person reads the number. It belongs in this message because it is evidence about whether these proposals can ever be trusted to place themselves.
   - End with the reminder that these are proposals only and nothing executes until Jack confirms in the interactive session.

3b. REFERENCE — write logs/digest/${TODAY}.md (overwrite if it exists), <= 1600 characters. Everything Jack CANNOT execute from the Agentic account. Open by saying plainly that this message is read-only context.
   - Opening line: 'US accounts: \$X across N accounts' — the cross-account grand total from the 'Full Portfolio Overview' section, carried verbatim along with the composition note that section states (cash, crypto). Call it US accounts, never 'total portfolio': that figure covers the US brokerages only and excludes the Korean holdings entirely, so calling it a portfolio total overstates nothing but understates everything else.
   - Reconcile outcome ONLY when the sheet matches — one short line saying so. Any actual drift goes to the Agentic message instead (step 3a), because it asks Jack to change something and this message is the one he can read and forget.
   - Trade-log check outcome ONLY when it is clean — one short line, e.g. 'all N filled Agentic trades logged'. Say it every clean day: this check exists because trades once went unlogged for seven weeks in silence, so the clean state has to be visible or its absence carries no information. Any finding goes to the Agentic message instead (step 3a).
   - If the overview flagged any position (notable drawdown ≤ −15% or gain ≥ +50%), one line: '⚠️ N positions flagged across all accounts — see full review'.
   - NO briefing note. The morning briefing already sent Jack its own act-now and this-week items on this same channel, hours earlier; repeating them here — at greater length, as this message did on 2026-08-11 with the TQQQ CPI item — spends his attention on something he has read and makes the afternoon message look like news when it is a ledger. If a briefing observation exists BECAUSE of an Agentic signal, it belongs in the Agentic message (step 3a) next to the proposal it explains.
   - Whenever an item concerns a holding outside the Agentic account, say so, so it is never mistaken for something this sleeve can act on.
4. Do NOT commit or push anything. logs/ is gitignored: this repo is public, and the journal names positions and balances. The reports stay on this machine and reach Jack through the digest below; the archive under $STOCK_DATA_DIR/trading-logs/ is where history is kept.

CRITICAL: Do NOT place or cancel any orders. Do not write to the Google Sheet. Only stage logs/reviews and logs/reconcile — never logs/digest."

# Both digests are per-day artefacts the wrapper delivers and nothing else reads;
# a stale one from an earlier run would otherwise be re-sent as today's.
rm -f "$REPO/logs/digest/${TODAY}.agentic.md"

echo "----- digest+commit step $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"
_run_with_retry "$PROMPT_D" "$TMP_D_JSON" "$TMP_D_ERR" D digest
RC_D=$?
# Did the deadline rewrite in PROMPT_D actually hold? Checked against the message
# that was written, not against the instruction that asked for it — this is the
# second fix for the same fault (flagged 2026-08-31, delivered again 09-01), and a
# prompt rule cannot report on itself. Never gates: a message with a stale deadline
# is still worth sending, what must not happen is nobody noticing it went out.
"$PYTHON" "$HERE/check-digest-deadlines.py" \
  "$REPO/logs/digest/${TODAY}.agentic.md" "$REPO/logs/digest/${TODAY}.md" \
  >> "$RUNLOG" 2>&1 || true

echo "----- digest stderr -----" >> "$RUNLOG"; cat "$TMP_D_ERR" >> "$RUNLOG" 2>/dev/null
echo "----- digest result json -----" >> "$RUNLOG"; cat "$TMP_D_JSON" >> "$RUNLOG" 2>/dev/null
_log_trader_usage "trading-review" "$(cat "$TMP_D_JSON" 2>/dev/null)"

# Overall rc: nonzero if any stage failed, so the cron wrapper surfaces a real
# failure (and, importantly, only a real one — each stage is now short).
RC=0
for r in "$RC_A" "$RC_B" "$RC_C" "$RC_D"; do
  [ "$r" -ne 0 ] && RC="$r"
done

# Machine-readable per-phase outcome for the cron wrapper. The wrapper used to
# quote `tail -3` of this log to explain a failure, which lands on the LAST job's
# JSON — job D, the one that usually succeeded — so a failure alert displayed a
# success payload. One line per phase, "<label> <rc> <what went wrong>", keeps the
# alert honest without making the wrapper parse a 300KB log.
STATUS_FILE="$REPO/logs/cron/${TODAY}.status"
: > "$STATUS_FILE"
for pair in "A:$RC_A:$TMP_A_JSON" "B:$RC_B:$TMP_B_JSON" "C:$RC_C:$TMP_C_JSON" "D:$RC_D:$TMP_D_JSON"; do
  lbl="${pair%%:*}"; rest="${pair#*:}"; prc="${rest%%:*}"; pjson="${rest#*:}"
  if [ "$prc" -eq 0 ]; then
    echo "$lbl 0 ok" >> "$STATUS_FILE"
  else
    reason="$("$PYTHON" - "$pjson" <<'PYEOF' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("no result JSON (the CLI produced no output)")
else:
    status = d.get("api_error_status")
    text = (d.get("result") or "").strip().split(". ")[0]
    print(f"HTTP {status}: {text}" if status else (text or "failed without a message"))
PYEOF
)"
    echo "$lbl $prc ${reason:-failed without a message}" >> "$STATUS_FILE"
  fi
done

echo "===== run-review end rc=$RC (A=$RC_A B=$RC_B C=$RC_C D=$RC_D) $(date '+%H:%M:%S %Z') =====" >> "$RUNLOG"
exit $RC
