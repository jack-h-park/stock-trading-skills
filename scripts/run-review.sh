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
#   B — reconcile: live positions vs holdings sheet -> logs/reconcile/<date>.md
#   C — overview: full cross-account portfolio view -> logs/reviews/<date>.overview.md
#   merge+digest: assemble overview into the review, write the Telegram digest,
#                 and do the single git add/commit/push.
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

CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
GIT="$(command -v git || echo /usr/bin/git)"
PYTHON="$(command -v python3 || echo /usr/bin/python3)"

cd "$REPO" || exit 1

# Stay in sync with the remote so the post-run push fast-forwards cleanly even if
# another machine (or a manual commit) pushed since the last run.
"$GIT" pull --rebase --autostash --quiet 2>/dev/null || true

TODAY="$(date +%Y-%m-%d)"
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
# The Google Sheets MCP requires the Hermes venv Python (has mcp + google-auth installed).
# Fall back to system python3 for local dev runs where the venv isn't present.
HERMES_VENV_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python"
[ -x "$HERMES_VENV_PYTHON" ] || HERMES_VENV_PYTHON="$PYTHON"
"$PYTHON" - "$MCP_CONFIG_FILE" "$ROBINHOOD_TOKEN" "$HERMES_VENV_PYTHON" "$SHEETS_MCP_SCRIPT" "$GCP_SA_KEY" << 'PYEOF'
import json, sys
out, token, py, script, sa = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
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
            "env": {"GOOGLE_APPLICATION_CREDENTIALS": sa}
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
  "Bash(git add:*)" "Bash(git commit:*)" "Bash(git push:*)" "Bash(git status:*)"
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
    c. For each TRIM signal: compute shares to sell (50% of held shares, rounded down; minimum 1 share), estimated proceeds (shares × current price), and which currently-open positions qualify as REDISTRIBUTE recipients (return-since-purchase > portfolio-average return, symbol in universe).
    d. Compute REDISTRIBUTE allocation: split proceeds equally among recipients, rounded to whole dollars; if no recipients, mark proceeds as 'held as cash'; if any allocation < minimum, drop the lowest outperformer and recompute.
    e. Show all numbers explicitly so the proposal is fully auditable: symbol, 20-day high, current price, drawdown %, shares to sell, estimated proceeds, redistribute-to list with dollar amounts.
4. Apply guardrails to BUY signals only (3 orders/day max, \$100 each, 20% position cap). Build the prioritized BUY list per the 'Prioritization (deterministic)' section of policy.md. TRIM signals are not subject to the buy order cap — they are exits. Show the drawdown number for each ranked BUY. Qualitative context goes only in 'Flags', never changes the ranked list.
4b. OBSERVATORY CONTEXT — read config/observatory-context.md and follow it. In short: read the summary file it names (\$STOCK_BRIEFING_SUMMARY_PATH, default ~/workspace/data/stock-management/outputs/stock-portfolio-observatory/briefing-summary.json); refuse it if schemaVersion > 1; keep ONLY items concerning a universe symbol or a symbol with an open Agentic position, at most 3; write them into the report's 'Flags' section as notes to Jack. If the file is missing, unreadable, or refused, write one line in Flags saying so and carry on — that is a normal state, not a failure. CRITICAL: these are whole-portfolio figures across every account and brokerage, denominated in KRW, and they already include the Agentic sleeve. Their percentages have a different denominator from the 20% Agentic position cap in config/guardrails.md — never compare them to it, and never let one satisfy or trigger a guardrail. This context must NOT change the ranked BUY list or its order, must NOT suppress or veto a signal, and must NOT be turned into a do-not-add rule.
5. Write the report to logs/reviews/${TODAY}.md following the format in skills/log/SKILL.md (append a timestamped section if the file already exists). Include both BUY proposals and TRIM+REDISTRIBUTE proposals in separate sections. PROPOSALS ONLY. Do not add a portfolio-overview section here. Do not commit.

${CRITICAL_RO}"

# ── Job B — Reconcile live positions vs the holdings sheet ────────────────────
PROMPT_B="You are running the scheduled, READ-ONLY reconcile step for Jack's brokerage accounts. Today is ${TODAY}. Produce ONLY the reconcile drift report.

Steps:
1. Read skills/reconcile/SKILL.md and config/holdings-sheet.md.
2. RECONCILE: using the Robinhood MCP get_equity_positions for the Long-term and Mid-term accounts in the mapping, and the mcp__google-drive__read_holdings tool (reads the full holdings sheet automatically; use mcp__google-drive__read_sheet if you need a custom range), diff Robinhood live positions against the sheet per the thresholds in the skill.
3. Write the drift report to logs/reconcile/${TODAY}.md. Report only — do NOT write to the sheet. Do not commit.

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
3. Write a SELF-CONTAINED markdown section to logs/reviews/${TODAY}.overview.md (overwrite if it exists) titled with a '## Full Portfolio Overview' heading, containing: (1) account-by-account summary table (account | # positions | total value), (2) a clearly labelled cross-account grand total line, (3) highlights table. This section is informational only — all proposals and execution remain scoped to the Agentic account. Do not write to logs/reviews/${TODAY}.md. Do not commit.

${CRITICAL_RO}"

# ── Run A, B, C concurrently; each writes its own file, none commit. ──────────
run_job() { # $1=prompt  $2=jsonfile  $3=errfile
  "$CLAUDE" -p "$1" \
    --model "$TRADER_CLAUDE_MODEL" \
    --output-format json \
    --mcp-config "$MCP_CONFIG_FILE" \
    --allowedTools "${READ_ALLOWED[@]}" \
    > "$2" 2> "$3"
}

echo "----- launching parallel jobs A/B/C $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"
run_job "$PROMPT_A" "$TMP_A_JSON" "$TMP_A_ERR" & PID_A=$!
run_job "$PROMPT_B" "$TMP_B_JSON" "$TMP_B_ERR" & PID_B=$!
run_job "$PROMPT_C" "$TMP_C_JSON" "$TMP_C_ERR" & PID_C=$!
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

# ── Digest + single commit. Thin step: reads the assembled local files only. ──
PROMPT_D="You are writing the notification digest and committing the scheduled portfolio review for ${TODAY}. The heavy work is already done and written to local files. Do NOT call any Robinhood or Google tools.

Steps:
1. Read logs/reviews/${TODAY}.md (contains the Agentic BUY/TRIM proposals and a 'Full Portfolio Overview' section) and logs/reconcile/${TODAY}.md (the reconcile drift report). If either file is missing, note it but continue with what exists.
2. BRIEFING CONTEXT: read ~/workspace/briefing/stock-portfolio-briefing/content/briefing-${TODAY}.json (if it exists). Extract the macro, moverNotes, and actions fields. Keep at most 2 observations directly relevant to the universe symbols or current open positions. If the file does not exist, skip silently.
3. DIGEST: write a notification digest to logs/digest/${TODAY}.md (overwrite if it exists), <= 1800 characters, for a Telegram push. Conversational tone — brief note from a trading assistant to Jack. Warm but concise; a little personality is fine; no markdown tables, no robotic headers.
   REQUIREMENTS — these exact facts must appear verbatim (do not paraphrase numbers); pull them from the files you read:
   - Opening line: total portfolio value across ALL accounts (e.g. 'Total portfolio: \$X across N accounts') — take the cross-account grand total from the 'Full Portfolio Overview' section.
   - Every ranked BUY proposal as '\$100 <SYM> (<drawdown>%)'
   - Every TRIM proposal as 'TRIM <SYM> <shares>sh (~\$<proceeds>) — <drawdown>% below 20d high' followed by 'REDISTRIBUTE → <SYM> \$<amount>, <SYM> \$<amount>' (or 'proceeds held as cash' if no recipients)
   - Whether there are no TRIM signals (say so explicitly)
   - Reconcile outcome (exact drift-alert count or that the sheet matches)
   - If the overview flagged any position (notable drawdown ≤ −15% or gain ≥ +50%), include a one-line summary: e.g. '⚠️ N positions flagged across all accounts — see full review'
   - Note that all are proposals only; execution happens in the interactive session with Jack's confirmation
   Lead with a one-line human summary. If the briefing had relevant observations, add a short '📰 Briefing note:' at the end — max 2 items, plain prose. End with a short non-pushy reminder that nothing executes automatically. This file is for notification delivery and is NOT committed.
4. Stage and commit: 'git add logs/reviews/${TODAY}.md logs/reconcile/${TODAY}.md' then 'git commit' with a concise message like 'review(${TODAY}): scheduled post-close review — N BUY signals, M reconcile alerts'. Then 'git push' (ignore push failure). Do NOT git add logs/digest.

CRITICAL: Do NOT place or cancel any orders. Do not write to the Google Sheet. Only stage logs/reviews and logs/reconcile — never logs/digest."

echo "----- digest+commit step $(date '+%H:%M:%S %Z') -----" >> "$RUNLOG"
"$CLAUDE" -p "$PROMPT_D" \
  --model "$TRADER_DIGEST_MODEL" \
  --output-format json \
  --allowedTools "${DIGEST_ALLOWED[@]}" \
  > "$TMP_D_JSON" 2> "$TMP_D_ERR"
RC_D=$?
echo "----- digest stderr -----" >> "$RUNLOG"; cat "$TMP_D_ERR" >> "$RUNLOG" 2>/dev/null
echo "----- digest result json -----" >> "$RUNLOG"; cat "$TMP_D_JSON" >> "$RUNLOG" 2>/dev/null
_log_trader_usage "trading-review" "$(cat "$TMP_D_JSON" 2>/dev/null)"

# Overall rc: nonzero if any stage failed, so the cron wrapper surfaces a real
# failure (and, importantly, only a real one — each stage is now short).
RC=0
for r in "$RC_A" "$RC_B" "$RC_C" "$RC_D"; do
  [ "$r" -ne 0 ] && RC="$r"
done
echo "===== run-review end rc=$RC (A=$RC_A B=$RC_B C=$RC_C D=$RC_D) $(date '+%H:%M:%S %Z') =====" >> "$RUNLOG"
exit $RC
