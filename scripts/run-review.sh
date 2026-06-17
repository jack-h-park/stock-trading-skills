#!/bin/bash
# Scheduled portfolio-review for the Robinhood Agentic account.
# Runs headless `claude -p` to: query Robinhood (live), compute dip signals,
# write a dated markdown review to logs/reviews/, and commit.
#
# READ-ONLY BY DESIGN: the --allowedTools whitelist below grants only Robinhood
# *read* tools + file write + git. place_equity_order / cancel_equity_order are
# NOT whitelisted, so this scheduled job physically cannot trade.
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

CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
GIT="$(command -v git || echo /usr/bin/git)"

cd "$REPO" || exit 1

# Stay in sync with the remote so the post-run push fast-forwards cleanly even if
# another machine (or a manual commit) pushed since the last run.
"$GIT" pull --rebase --autostash --quiet 2>/dev/null || true

TODAY="$(date +%Y-%m-%d)"
NOW="$(date '+%Y-%m-%d %H:%M:%S %Z')"
RUNLOG="$REPO/logs/cron/${TODAY}.run.log"
mkdir -p "$REPO/logs/cron" "$REPO/logs/digest"

echo "===== run-review start $NOW =====" >> "$RUNLOG"

# Skip US market holidays / weekends cheaply: launchd already restricts to Mon-Fri,
# but skip if it's a weekend for any manual run. (Holiday skipping is left to the
# agent: with no fresh session the dip scan simply reflects the last close.)
DOW="$(date +%u)"  # 1=Mon .. 7=Sun
if [ "$DOW" -ge 6 ] && [ "${FORCE:-0}" != "1" ]; then
  echo "weekend ($DOW) — skipping (set FORCE=1 to run anyway)" >> "$RUNLOG"
  exit 0
fi

# NOTE: internal double-quotes inside the PROMPT string must be escaped as \".
# Dollar signs passed literally to Claude must be escaped as \$.
PROMPT="You are running the scheduled, READ-ONLY portfolio-review for this repo's Robinhood Agentic trading account. Today is ${TODAY}.

Steps:
1. Read strategy/policy.md, config/guardrails.md, config/trim-policy.md, providers/robinhood/adapter.md, providers/robinhood/capabilities.md, and skills/portfolio-review/SKILL.md + skills/log/SKILL.md.
2. Using the Robinhood MCP (tools are prefixed mcp__claude_ai_Robinhood__), for the Agentic account in the adapter: get_portfolio and get_equity_positions; get_equity_quotes and get_equity_historicals (interval=day, last ~30 days) for the universe symbols in policy.md.
3. BUY SIGNALS — Compute each universe symbol's trailing 20-trading-day high and its drawdown vs the latest price. Flag a BUY signal when price is >= 5% below the 20-day high, per policy.md entry rules.
3b. TRIM SIGNALS — For each symbol with an open position in the Agentic account, check the trim conditions from config/trim-policy.md:
    a. Skip if: position market value < minimum, kill-switch active, position was opened < 3 calendar days ago, or a stop-loss/take-profit signal already applies (use the stronger exit).
    b. Compute the 20-day high (same historicals data from step 2). If current price <= (20-day high x (1 - threshold)), flag a TRIM signal.
    c. For each TRIM signal: compute shares to sell (50% of held shares, rounded down; minimum 1 share), estimated proceeds (shares x current price), and which currently-open positions qualify as REDISTRIBUTE recipients (return-since-purchase > portfolio-average return, symbol in universe).
    d. Compute REDISTRIBUTE allocation: split proceeds equally among recipients, rounded to whole dollars; if no recipients, mark proceeds as held-as-cash; if any allocation < minimum, drop the lowest outperformer and recompute.
    e. Show all numbers explicitly so the proposal is fully auditable: symbol, 20-day high, current price, drawdown %, shares to sell, estimated proceeds, redistribute-to list with dollar amounts.
4. Apply guardrails to BUY signals only (3 orders/day max, \$100 each, 20% position cap). Build the prioritized BUY list per the Prioritization section of policy.md. TRIM signals are not subject to the buy order cap — they are exits. Show the drawdown number for each ranked BUY. Qualitative context goes only in a Flags note, never changes the ranked list.
5. Write the report to logs/reviews/${TODAY}.md following the format in skills/log/SKILL.md (append a timestamped section if the file already exists). Include both BUY proposals and TRIM+REDISTRIBUTE proposals in separate sections. PROPOSALS ONLY.
6. RECONCILE: read skills/reconcile/SKILL.md and config/holdings-sheet.md. Using get_equity_positions for the Long-term and Mid-term accounts in the mapping, and the Google Drive tool mcp__claude_ai_Google_Drive__read_file_content to read the sheet fileId, diff Robinhood live positions against the sheet per the thresholds. Write the drift report to logs/reconcile/${TODAY}.md. Report only — do NOT write to the sheet.
7. BRIEFING CONTEXT: read ~/workspace/briefing/stock-portfolio-briefing/content/briefing-${TODAY}.json (if it exists). Extract the macro, moverNotes, and actions fields. Identify any items directly relevant to the universe symbols or current open positions. Keep at most 2 relevant observations. If the file does not exist, skip silently.
8. DIGEST: write a notification digest to logs/digest/${TODAY}.md (overwrite if it exists), <= 1800 characters, for a Telegram push. Conversational tone — brief note from a trading assistant to Jack. Warm but concise; a little personality is fine; no markdown tables, no robotic headers.
   REQUIREMENTS — these exact facts must appear verbatim (do not paraphrase numbers):
   - Every ranked BUY proposal as '\$100 <SYM> (<drawdown>%)'
   - Every TRIM proposal as 'TRIM <SYM> <shares>sh (~\$<proceeds>) — <drawdown>% below 20d high' followed by 'REDISTRIBUTE to <SYM> \$<amount>' (or 'proceeds held as cash' if no recipients)
   - Whether there are no TRIM signals (say so explicitly)
   - Reconcile outcome (exact drift-alert count or that the sheet matches)
   - Note that all are proposals only; execution happens in the interactive session with Jack's confirmation
   Lead with a one-line human summary. If the briefing had relevant observations (step 7), add a short Briefing note paragraph at the end — max 2 items, plain prose. End with a short non-pushy reminder that nothing executes automatically. This file is for notification delivery and is NOT committed.
9. Stage and commit: 'git add logs/reviews/${TODAY}.md logs/reconcile/${TODAY}.md' then 'git commit'. Then 'git push' (ignore push failure). Do NOT git add logs/digest.

CRITICAL: This is READ-ONLY. Do NOT place or cancel any orders under any circumstances. Do not call place_equity_order or cancel_equity_order. Do not write to the Google Sheet."

"$CLAUDE" -p "$PROMPT" \
  --output-format text \
  --allowedTools \
    "mcp__claude_ai_Robinhood__get_accounts" \
    "mcp__claude_ai_Robinhood__get_portfolio" \
    "mcp__claude_ai_Robinhood__get_equity_positions" \
    "mcp__claude_ai_Robinhood__get_equity_quotes" \
    "mcp__claude_ai_Robinhood__get_equity_historicals" \
    "mcp__claude_ai_Robinhood__get_equity_orders" \
    "mcp__claude_ai_Robinhood__get_equity_tradability" \
    "mcp__claude_ai_Google_Drive__read_file_content" \
    "mcp__claude_ai_Google_Drive__get_file_metadata" \
    "Read" "Write" "Edit" "Grep" "Glob" \
    "Bash(git add:*)" "Bash(git commit:*)" "Bash(git push:*)" "Bash(git status:*)" \
  >> "$RUNLOG" 2>&1

RC=$?
echo "===== run-review end rc=$RC $(date '+%H:%M:%S %Z') =====" >> "$RUNLOG"
exit $RC
