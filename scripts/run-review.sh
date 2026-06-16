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

PROMPT="You are running the scheduled, READ-ONLY portfolio-review for this repo's Robinhood Agentic trading account. Today is ${TODAY}.

Steps:
1. Read strategy/policy.md, config/guardrails.md, providers/robinhood/adapter.md, providers/robinhood/capabilities.md, and skills/portfolio-review/SKILL.md + skills/log/SKILL.md.
2. Using the Robinhood MCP (tools are prefixed mcp__claude_ai_Robinhood__), for the Agentic account in the adapter: get_portfolio and get_equity_positions; get_equity_quotes and get_equity_historicals (interval=day, last ~30 days) for the universe symbols in policy.md.
3. Compute each universe symbol's trailing 20-trading-day high and its drawdown vs the latest price. Flag a BUY signal when price is >= 5% below the 20-day high, per policy.md entry rules.
4. Apply guardrails (3 orders/day max, \$100 each, 20% position cap). Build the prioritized action list by following the 'Prioritization (deterministic)' section of policy.md EXACTLY: rank eligible signals by drawdown depth (deepest first), tie-break alphabetically, take the top N = remaining order slots. Do NOT reorder by conviction, news, or any qualitative judgment. Show the drawdown number used for each rank so the ordering is auditable. Any qualitative context goes only in a 'Flags' note to the user, never changing the ranked list.
5. Write the report to logs/reviews/${TODAY}.md following the format in skills/log/SKILL.md (append a timestamped section if the file already exists). PROPOSALS ONLY.
6. RECONCILE: read skills/reconcile/SKILL.md and config/holdings-sheet.md. Using get_equity_positions for the Long-term and Mid-term accounts in the mapping, and the Google Drive tool mcp__claude_ai_Google_Drive__read_file_content to read the sheet fileId, diff Robinhood live positions against the sheet per the thresholds. Write the drift report to logs/reconcile/${TODAY}.md. Report only — do NOT write to the sheet.
7. DIGEST: write a notification digest to logs/digest/${TODAY}.md (overwrite if it exists), <= 1400 characters, for a Telegram push. Write it in a natural, conversational tone — like a brief note from a trading assistant to the account owner (Jack), not a machine report. Warm but concise; a little personality is fine; no markdown tables, no robotic headers.
   REQUIREMENTS — these exact facts must appear, verbatim and unaltered (do not paraphrase numbers): every ranked BUY proposal as '\$100 <SYM> (<drawdown>%)' with the same symbols, order, and percentages as the report; whether there are SELL/exit proposals (state them or say there are none); and the reconcile outcome (exact drift-alert count, or that the sheet matches). Also note it's read-only/proposals-only and which session they'd execute.
   Lead with a one-line human summary (e.g. how many buy signals, market context). Weave the numbers into prose or a short bulleted list, but the symbols/percentages/counts must be exactly as computed — never round, invent, or drop them. End with a short, non-pushy reminder that nothing is executed automatically. This file is for notification delivery and is NOT committed.
8. Stage and commit: 'git add logs/reviews/${TODAY}.md logs/reconcile/${TODAY}.md' then 'git commit'. Then 'git push' (ignore push failure). Do NOT git add logs/digest.

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
