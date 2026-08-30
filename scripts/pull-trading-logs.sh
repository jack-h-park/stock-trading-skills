#!/usr/bin/env bash
# pull-trading-logs.sh — copy the journal from the machine that writes it to the
# archive that keeps it.
#
# The review runs on the always-on host and writes logs/reviews, logs/reconcile
# and logs/trades there. Until 2026-08-25 those files were tracked in git, so
# every machine got them by pulling. Making this repo public ended that: logs/
# names positions, quantities and balances every weekday, so it is gitignored and
# the review commits nothing.
#
# What that left behind is a claim with nothing implementing it. run-review.sh
# tells its own digest step that "the archive under $STOCK_DATA_DIR/trading-logs/
# is where history is kept", and on 2026-08-30 that directory did not exist on the
# host at all — the only copy was a one-time snapshot taken on the laptop during
# the migration, frozen at 2026-08-24. The journal had become a single untracked
# copy on one machine, which is the state a decision journal is least able to
# survive: nothing would have reported its loss.
#
# ONE WAY, ADDITIVE. The host writes, the archive keeps. There is deliberately no
# --delete: an entry that leaves the host (a re-clone, a reset, a hand-tidied
# directory) must not be erased from the archive as a side effect. The archive is
# allowed to hold more than the host does, and it already does — the pre-floor
# backlog note lives in both.
#
#   logs/reviews    logs/reconcile    logs/trades   -> archived
#   logs/cron       -> not archived; raw run output, regenerated every day
#   logs/digest     -> not archived; overwritten each run, and Jack reads it in Discord
#
# Run it on the machine that holds the archive. Reads on the host, writes locally.

set -uo pipefail

HOST="${TRADER_LOG_HOST:-hermes-runner@imac-hermes}"
REMOTE_REPO="${TRADER_LOG_REMOTE_REPO:-workspace/ai-assets/jackhpark-stock-trading-skills}"
ARCHIVE="${TRADER_LOG_ARCHIVE:-$HOME/workspace/data/stock-management/trading-logs}"
DRY=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY="--dry-run"; shift ;;
    --host) HOST="$2"; shift 2 ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Fail on an unreachable host rather than reporting "0 files" as success. A sync
# that silently transfers nothing is indistinguishable from a quiet day.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "test -d ~/$REMOTE_REPO/logs" 2>/dev/null; then
  echo "cannot reach $HOST:~/$REMOTE_REPO/logs — nothing pulled" >&2
  exit 1
fi

mkdir -p "$ARCHIVE"
echo "pulling journal from $HOST -> $ARCHIVE${DRY:+  (dry run)}"

rc=0
for sub in reviews reconcile trades; do
  if ! ssh -o BatchMode=yes "$HOST" "test -d ~/$REMOTE_REPO/logs/$sub" 2>/dev/null; then
    echo "  $sub: not present on the host — skipped"
    continue
  fi
  mkdir -p "$ARCHIVE/$sub"
  # -a to keep timestamps, which are the only record of when an entry was written
  # once the file leaves git. --itemize-changes so a run that moves nothing says so.
  rsync -a --human-readable --itemize-changes $DRY \
    "$HOST:$REMOTE_REPO/logs/$sub/" "$ARCHIVE/$sub/" || rc=$?
done

if [ "$rc" -ne 0 ]; then
  echo "one or more directories failed to transfer (rc=$rc)" >&2
  exit "$rc"
fi
echo "done"
