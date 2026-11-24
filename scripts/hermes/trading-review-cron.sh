#!/usr/bin/env bash
# trading-review-cron.sh — Hermes cron `--no-agent --script` entrypoint.
#
# Hermes runs this on schedule and delivers its STDOUT verbatim to the configured
# channel (Telegram). So: run the heavy review (codex → Robinhood/Drive, which logs
# to its own run log and emits no stdout), then print only the short digest.
#
# Empty stdout = Hermes stays silent (weekend/holiday/skip). That's intentional.
#
# Exit codes. A run that did not produce the review exits 1; everything else
# exits 0. This used to exit 0 on failure too, on the reasoning that stdout is the
# delivery channel and a nonzero exit becomes a bare "Cron failed" error. Neither
# half holds: Hermes delivers a failing no-agent script's stdout as well (under a
# "script failed" heading, with the exit code), and exit 0 recorded every failure
# as last_status=ok — which is all cron-health-watchdog reads. On 2026-10-05 and
# 10-06 the review failed both days and the fleet's failure watch saw two healthy
# runs; the only trace was a trader-topic message saying "see the run log".
#
# Installed to ~/.hermes/profiles/trader/scripts/ by scripts/hermes/install-cron.sh.
# The trading repo location is resolved via $TRADING_AGENT_REPO (default below).

set -uo pipefail

REPO="${TRADING_AGENT_REPO:-$HOME/workspace/ai-assets/jackhpark-stock-trading-skills}"
RUNNER="$REPO/scripts/run-review.sh"
DATE_ISO="$(date +%F)"
RUNLOG="$REPO/logs/cron/${DATE_ISO}.run.log"

# Enrich PATH so python3 is reachable (hermes cron runs with a minimal PATH).
for _d in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH";; esac
done
export PATH="$PATH:/usr/bin:/bin"
unset _d

# Pin the codex CLI this review runs, scoped to this cron runner. run-review.sh
# takes codex from PATH, and the host-wide one (Homebrew) moves on its own
# schedule. When the trader model moved to a newer generation on 2026-11-18, the
# review needed a newer codex than Homebrew had, so a private install was put
# first on PATH — by hand, in the deployed copy of this file only. Copying the
# repo version over it would have dropped the pin without a word, and the next
# review would have run the older CLI against a model it may not serve.
#
# The pinned install is used only when it exists, so a host without one keeps
# the PATH codex. To move the pin, install the new version beside the old one
# and set TRADER_REVIEW_CODEX_VERSION in the trader profile .env.
_pin="$HOME/.local/share/codex-trader-review-cli/${TRADER_REVIEW_CODEX_VERSION:-0.169.1}/node_modules/.bin"
if [ -x "$_pin/codex" ]; then
  PATH="$_pin:$PATH"
elif [ -n "${TRADER_REVIEW_CODEX_VERSION:-}" ]; then
  echo "trading-review: TRADER_REVIEW_CODEX_VERSION=$TRADER_REVIEW_CODEX_VERSION has no codex at $_pin; using $(command -v codex || echo 'no codex on PATH')" >&2
fi
unset _pin

# $REPO comes from the profile .env, which outranks the default above. When the
# checkout is renamed or moved and that value is not updated with it, every path
# built from it points at nothing. Check the directory itself, not just the
# runner: "the configured repo does not exist" names the one thing to fix, while
# "runner not found" reads like a missing file inside a repo that is fine.
#
# 2026-08-25 the checkout was renamed to jackhpark-stock-trading-skills and
# TRADING_AGENT_REPO kept pointing at the old name. From 08-26 the market-check
# call above could not find its script, exited nonzero, and (stderr discarded
# back then) was read as "not a trading day" — so the review silently did not run
# and the Telegram message said the market was closed on an ordinary Wednesday.
# Alert on stdout and exit nonzero — see "Exit codes" below.
if [ ! -d "$REPO" ]; then
  echo "⚠️ trading-review $DATE_ISO: configured repo does not exist at $REPO — review did not run. Set TRADING_AGENT_REPO in the trader profile .env to the current checkout path."
  exit 1
fi

# Skip on non-NYSE trading days and notify so the skip is visible in Telegram.
#
# market-check.py's contract is "exit 0 = trading day, exit 1 = not" — but an
# uncaught Python exception also exits 1, indistinguishable from a clean skip by
# code alone. On 2026-08-26 (an ordinary Wednesday, no NYSE holiday) this branch
# fired and the whole review never ran; re-running market-check.py for the same
# date afterward returned 0. So a crash was silently read as "market closed."
# Capture stderr and only treat the skip as real when the script said so cleanly
# — an error runs the review anyway, since guessing "closed" from a crash risks
# losing a real trading day, while guessing "open" costs nothing but exchange
# holidays already return no new close data on their own.
MARKET_CHECK_ERR="$(mktemp)"
python3 "$REPO/scripts/hermes/market-check.py" 2>"$MARKET_CHECK_ERR"
MARKET_CHECK_RC=$?
if [ "$MARKET_CHECK_RC" -ne 0 ]; then
  if [ -s "$MARKET_CHECK_ERR" ]; then
    echo "⚠️ ${DATE_ISO}: market-check.py errored (rc=$MARKET_CHECK_RC) instead of a clean trading-day answer — running the review anyway. $(head -c 300 "$MARKET_CHECK_ERR" | tr '\n' ' ')"
  else
    rm -f "$MARKET_CHECK_ERR"
    echo "📅 ${DATE_ISO} 주식 개장일이 아니어서 오늘 trading review는 진행하지 않았습니다."
    exit 0
  fi
fi
rm -f "$MARKET_CHECK_ERR"

[ -f "$RUNNER" ] || { echo "⚠️ trading-review $DATE_ISO: runner not found at $RUNNER — review did not run."; exit 1; }

# Run the review+reconcile. It self-logs to logs/cron/<date>.run.log and emits no
# stdout of its own; on weekends it skips and writes no digest. Its stderr goes to
# that log too: Hermes shows a script's stderr only when the script exits nonzero,
# so anything the runner said there before it redirected itself was lost.
mkdir -p "$REPO/logs/cron"
bash "$RUNNER" 2>>"$RUNLOG"
RC=$?

AGENTIC="$REPO/logs/digest/${DATE_ISO}.agentic.md"
STATUS="$REPO/logs/cron/${DATE_ISO}.status"

# The Agentic message goes to Discord, sent from here. This script's stdout is what
# Hermes delivers to Telegram, and on a normal day it is now empty, which Hermes
# treats as silent: the read-only Telegram digest that used to follow was dropped on
# 2026-11-18 (docs/decisions.md §24). Stdout still carries the two things that must
# reach Jack somewhere: a failure alert, and the Agentic message itself when Discord
# cannot take it (no credentials, or a failed send).

# Hermes cron --script subprocesses do not inherit the profile .env, and the
# repo's _env.sh carries only PATH. Pull the two
# Discord keys out by name rather than sourcing the whole file, which would also
# re-set TRADING_AGENT_REPO and PATH from a file this script has already resolved.
PROFILE_ENV="${TRADER_PROFILE_ENV:-$HOME/.hermes/profiles/trader/.env}"
if [ -r "$PROFILE_ENV" ]; then
  for _k in DISCORD_BOT_TOKEN DISCORD_HOME_CHANNEL; do
    _v="$(grep -E "^${_k}=" "$PROFILE_ENV" | tail -1 | cut -d= -f2-)"
    [ -n "$_v" ] && export "$_k=$_v"
  done
  unset _k _v
fi

# The read-only Telegram digest is gone. It carried the cross-account US total, a
# flag count that pointed at a review file the reader could not open, and two
# clean-check lines. The morning briefing already covers the portfolio on Telegram,
# the checks now close the Agentic message, and the total stays in the review. Its
# predecessor, the Korean line, was removed for the same reason earlier: it repeated
# what the morning briefing had sent hours before.

# The usage footer send_discord.py appends is computed from Hermes' session
# store, which cannot see this job: run-review.sh spends its money in four
# agent-CLI SUBPROCESSES (`claude -p` until 2026-09-28, `codex exec` since), one
# level below the runtime that would have recorded it. Sending with nothing to attribute used to produce "— no LLM · 100%
# deterministic" on a message that had just cost $6.49 (2026-09-09, four calls,
# in logs/cron/<date>.run.log and in ~/.hermes/logs/trader-usage.jsonl).
#
# So name the log the subprocesses already write. It stays the one source of
# truth for this spend — the control plane's collector reads the same file for
# the Observatory rollup, and the footer's ledger row is written unattributed so
# the two readers cannot double-count one run.
TRADER_USAGE_LOG="${TRADER_USAGE_LOG:-$HOME/.hermes/logs/trader-usage.jsonl}"

deliver_agentic() {
  [ -f "$AGENTIC" ] || return 0
  local sender="$HOME/workspace/ai-assets/jackhpark-hermes-control-plane/gateway/scripts/send_discord.py"
  if [ -n "${DISCORD_BOT_TOKEN:-}" ] && [ -n "${DISCORD_HOME_CHANNEL:-}" ] && [ -f "$sender" ]; then
    if python3 "$sender" --bot-token "$DISCORD_BOT_TOKEN" --channel-id "$DISCORD_HOME_CHANNEL" \
         --usage-unit-key trading-review --usage-log "$TRADER_USAGE_LOG" \
         < "$AGENTIC" >/dev/null 2>&1; then
      return 0
    fi
    # A failed send must not swallow the proposals — fall through to stdout.
    echo "⚠️ Agentic proposals could not be sent to Discord; delivering here instead."
  fi
  cat "$AGENTIC"
}

# Name the phases that actually failed, from the runner's status file. Reading the
# tail of the run log instead lands on the LAST job's result JSON — normally the
# digest step, which usually succeeds — so the alert used to announce a failure
# while quoting a success payload.
#
# Phases that failed for the same reason are named together: when the model is
# the problem, all three jobs say the same sentence, and three copies of it
# pushed the one fact that mattered off a phone screen.
if [ "$RC" -ne 0 ]; then
  FAILED="$(awk '
    BEGIN { split("A=signals B=reconcile C=overview D=digest P=preflight", p, " ")
            for (i in p) { split(p[i], kv, "="); name[kv[1]] = kv[2] } }
    NF >= 2 && $2 != "0" {
      lbl = ($1 in name) ? name[$1] : $1
      why = $0; sub(/^[^ ]+ [^ ]+ ?/, "", why); if (why == "") why = "failed without a message"
      if (!(why in seen)) { order[++n] = why; seen[why] = lbl } else seen[why] = seen[why] "+" lbl
    }
    END { for (i = 1; i <= n; i++) printf "%s%s — %s", (i > 1 ? "; " : ""), seen[order[i]], order[i] }
  ' "$STATUS" 2>/dev/null)"

  # No status file means the runner died before it could write one. Quote the
  # last error line this run left in its log rather than pointing at the file:
  # the reader is on a phone and the log is on the ops host.
  if [ -z "$FAILED" ]; then
    LAST_ERR="$(awk '/^===== run-review start/ { line = "" }
                     /ERROR|unbound variable|command not found|syntax error|No such file/ { line = $0 }
                     END { print line }' "$RUNLOG" 2>/dev/null | cut -c1-300)"
    FAILED="${LAST_ERR:-no error line in logs/cron/${DATE_ISO}.run.log}"
  fi

  # A partial failure still leaves real work on disk. Suppressing the digest
  # because one phase died threw away the proposals the surviving phases had
  # already produced and paid for, so lead with the warning and deliver anyway.
  if [ -f "$AGENTIC" ]; then
    echo "⚠️ trading-review PARTIAL $DATE_ISO — ${FAILED}"
    echo "The Agentic message is built from the phases that did finish and goes to Discord (or follows here if Discord cannot take it); anything owned by a failed phase is missing from it."
    deliver_agentic
  else
    echo "⚠️ trading-review FAILED $DATE_ISO (rc=$RC) — ${FAILED}"
  fi
  exit 1
fi

# Proposals to Discord. Nothing on stdout when that works, so Telegram stays silent.
deliver_agentic

exit 0
