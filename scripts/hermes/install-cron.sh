#!/usr/bin/env bash
# install-cron.sh — declare the trading-review Hermes cron job under the `trader`
# profile (ops host only). Versioned, reproducible source for the job; the job
# itself lives in the trader profile's runtime cron store once created.
#
# Mirrors hermes-control-plane's gateway/scripts/cron-install-ops.sh pattern:
#   - installs the --no-agent wrapper into ~/.hermes/scripts/
#   - creates the cron job PAUSED (installing != enabling)
#
# Delivery: --deliver telegram → the trader profile must have TELEGRAM_BOT_TOKEN +
# TELEGRAM_CHAT_ID configured (gateway/env/trader.env on the ops host). Until then,
# create with --deliver local to dry-run into the cron log.
#
# The delivery target is required and has no default: it is the first argument,
# or TRADER_CRON_DELIVER when there is none, so the host can name a specific chat
# — including a forum topic, as `telegram:<chat_id>:<thread_id>` — WITHOUT that id
# living in this repository. This repo is public; a chat id is not a credential,
# but it is private infrastructure. A bare `telegram` used to be the default, which
# resolves to the profile's home channel and so is not necessarily where the live
# job delivers; a job created there sends its first message to the wrong place.
#
# Which host may run this is decided by the role marker ~/.hermes/role (`ops`), not
# by the account name: the ops account has a different name on different hosts.
#
# Usage (on the ops host), after the trader profile is bootstrapped:
#   TRADING_AGENT_REPO=~/workspace/ai-assets/jackhpark-stock-trading-skills \
#     scripts/hermes/install-cron.sh telegram:<chat>[:<thread>]|telegram|local
#   (or export TRADER_CRON_DELIVER and pass no argument)
# Then enable in the maintenance window:
#   <hermes> --profile trader cron resume trading-review
set -euo pipefail

# Refuse to run anywhere but the host that declares itself the ops host. The
# declaration is ~/.hermes/role containing `ops` — the marker the control plane's
# own installers read — and not the account name. A marker cannot be created by
# cloning the repo, which is also why an account-name test was the wrong guard for
# a machine that has the repos but must not run the crons.
ROLE_FILE="${HERMES_REAL_HOME:-$HOME}/.hermes/role"
ROLE=""
[[ -r "$ROLE_FILE" ]] && ROLE="$(tr -d '[:space:]' < "$ROLE_FILE")"
if [[ "$ROLE" != "ops" ]]; then
  echo "install-cron.sh is ops-host-only: $ROLE_FILE must contain 'ops' (found '${ROLE:-<missing>}')." >&2
  echo "  Declare it once, on the host that runs operations: printf 'ops\\n' > ~/.hermes/role" >&2
  exit 1
fi

DELIVER="${1:-${TRADER_CRON_DELIVER:-}}"
if [[ -z "$DELIVER" ]]; then
  echo "no delivery target: pass one as the first argument or set TRADER_CRON_DELIVER." >&2
  echo "  telegram:<chat_id>[:<thread_id>] | telegram | local" >&2
  exit 2
fi
PROFILE="trader"
JOB_NAME="trading-review"
SCHEDULE="30 13 * * 1-5"   # weekdays 13:30 local (= 16:30 ET, 30 min after US close)

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${TRADING_AGENT_REPO:-$(cd "$HERE/../.." && pwd)}"
SCRIPTS_DIR="$HOME/.hermes/profiles/$PROFILE/scripts"   # Hermes --script resolves under $HERMES_HOME
WRAPPER_SRC="$HERE/trading-review-cron.sh"
WRAPPER_DST="$SCRIPTS_DIR/trading-review-cron.sh"

PY="$HOME/.hermes/hermes-agent/venv/bin/python"
hermes() { "$PY" -m hermes_cli.main "$@"; }

[ -f "$WRAPPER_SRC" ] || { echo "wrapper not found: $WRAPPER_SRC" >&2; exit 1; }

# 1. Install the wrapper into the PROFILE's scripts dir. Hermes resolves --script
#    relative to $HERMES_HOME (= the profile root), i.e. <profile>/scripts/.
mkdir -p "$SCRIPTS_DIR"
cp "$WRAPPER_SRC" "$WRAPPER_DST"
chmod +x "$WRAPPER_DST"
echo "installed wrapper -> $WRAPPER_DST"

# 1b. Profile runtime settings for clean, schedulable delivery (idempotent):
#     - cron_mode allow: let cron jobs run without per-tick approval
#     - cron.wrap_response false: deliver raw script stdout (no "Cronjob Response /
#       To stop or manage this job" boilerplate around the digest)
hermes --profile "$PROFILE" config set approvals.cron_mode allow >/dev/null 2>&1 || true
hermes --profile "$PROFILE" config set cron.wrap_response false >/dev/null 2>&1 || true
echo "set approvals.cron_mode=allow, cron.wrap_response=false"

# 2. Create the cron job PAUSED, unless it already exists (idempotent).
#    `cron list` hides paused jobs, so the existence check needs --all: without it a
#    job this script created paused looks absent on the next run and is created twice.
#    `cron create` and `cron pause` exit 0 even when they did nothing, so the state
#    is read back rather than assumed.
job_state() {
  hermes --profile "$PROFILE" cron list --all 2>/dev/null \
    | awk -v n="$1" '/^ +[0-9a-f]+ \[/ {st=$2} $1 == "Name:" && $2 == n {print st; exit}' || true
}

if [[ -n "$(job_state "$JOB_NAME")" ]]; then
  echo "[skip] cron job '$JOB_NAME' already exists — leaving it untouched."
else
  echo "[create] $JOB_NAME ($SCHEDULE, deliver=$DELIVER, --no-agent)"
  hermes --profile "$PROFILE" cron create "$SCHEDULE" \
    --name "$JOB_NAME" \
    --script "trading-review-cron.sh" \
    --no-agent \
    --deliver "$DELIVER"
  hermes --profile "$PROFILE" cron pause "$JOB_NAME"
  STATE="$(job_state "$JOB_NAME")"
  if [[ "$STATE" != "[paused]" ]]; then
    echo "job '$JOB_NAME' is not paused after create (state: ${STATE:-<not found>})." >&2
    echo "  Check it, and pause it before a gateway starts: $PY -m hermes_cli.main --profile $PROFILE cron pause $JOB_NAME" >&2
    exit 1
  fi
  echo "created PAUSED. Resume when ready:"
  echo "  $PY -m hermes_cli.main --profile $PROFILE cron resume $JOB_NAME"
fi

echo
echo "Verify:  $PY -m hermes_cli.main --profile $PROFILE cron list --all"
