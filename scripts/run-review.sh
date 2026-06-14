#!/bin/bash
# Scheduled portfolio-review for the Robinhood Agentic account.
# Runs headless `claude -p` to: query Robinhood (live), compute dip signals,
# write a dated markdown review to logs/reviews/, and commit.
#
# READ-ONLY BY DESIGN: the --allowedTools whitelist below grants only Robinhood
# *read* tools + file write + git. place_equity_order / cancel_equity_order are
# NOT whitelisted, so this scheduled job physically cannot trade.
#
# Invoked by ~/Library/LaunchAgents/com.jackpark.trading-agent-review.plist

set -uo pipefail

export PATH="/Users/jackpark/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="/Users/jackpark/workspace/ai-assets/jackhpark-trading-agent"
CLAUDE="/Users/jackpark/.local/bin/claude"
GIT="/usr/bin/git"

cd "$REPO" || exit 1

TODAY="$(date +%Y-%m-%d)"
NOW="$(date '+%Y-%m-%d %H:%M:%S %Z')"
RUNLOG="$REPO/logs/cron/${TODAY}.run.log"

echo "===== run-review start $NOW =====" >> "$RUNLOG"

# Skip US market holidays / weekends cheaply: launchd already restricts to Mon-Fri,
# but skip if it's a weekend for any manual run. (Holiday skipping is left to the
# agent: with no fresh session the dip scan simply reflects the last close.)
DOW="$(date +%u)"  # 1=Mon .. 7=Sun
if [ "$DOW" -ge 6 ] && [ "${FORCE:-0}" != "1" ]; then
  echo "weekend ($DOW) — skipping (set FORCE=1 to run anyway)" >> "$RUNLOG"
  exit 0
fi

PROMPT="You are running the scheduled, READ-ONLY portfolio-review for this repo's Robinhood Agentic trading account. Today is ${TODAY}.

Steps:
1. Read strategy/policy.md, config/guardrails.md, providers/robinhood/adapter.md, providers/robinhood/capabilities.md, and skills/portfolio-review/SKILL.md + skills/log/SKILL.md.
2. Using the Robinhood MCP (tools are prefixed mcp__claude_ai_Robinhood__), for the Agentic account in the adapter: get_portfolio and get_equity_positions; get_equity_quotes and get_equity_historicals (interval=day, last ~30 days) for the universe symbols in policy.md.
3. Compute each universe symbol's trailing 20-trading-day high and its drawdown vs the latest price. Flag a BUY signal when price is >= 5% below the 20-day high, per policy.md entry rules.
4. Apply guardrails (3 orders/day max, \$100 each, 20% position cap). Build the prioritized action list by following the 'Prioritization (deterministic)' section of policy.md EXACTLY: rank eligible signals by drawdown depth (deepest first), tie-break alphabetically, take the top N = remaining order slots. Do NOT reorder by conviction, news, or any qualitative judgment. Show the drawdown number used for each rank so the ordering is auditable. Any qualitative context goes only in a 'Flags' note to the user, never changing the ranked list.
5. Write the report to logs/reviews/${TODAY}.md following the format in skills/log/SKILL.md (append a timestamped section if the file already exists). PROPOSALS ONLY.
6. RECONCILE: read skills/reconcile/SKILL.md and config/holdings-sheet.md. Using get_equity_positions for the Long-term and Mid-term accounts in the mapping, and the Google Drive tool mcp__claude_ai_Google_Drive__read_file_content to read the sheet fileId, diff Robinhood live positions against the sheet per the thresholds. Write the drift report to logs/reconcile/${TODAY}.md. Report only — do NOT write to the sheet.
7. Stage and commit: 'git add logs/reviews/${TODAY}.md logs/reconcile/${TODAY}.md' then 'git commit'. Then 'git push' (ignore push failure).

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
