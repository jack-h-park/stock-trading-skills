# Operations runbook

Day-to-day commands for running, hosting, verifying, and troubleshooting the agent.
For design context see [architecture.md](architecture.md).

## Where it runs

The scheduled job is hosted on the always-on iMac **`hermes-runner@imac-hermes`** (macOS,
US Pacific time) so it isn't skipped when the laptop sleeps.

- LaunchAgent label: `com.jackpark.trading-agent-review`
- Schedule: weekdays (Mon–Fri) **13:30 PT = 16:30 ET** (30 min after the US close)
- Repo on host: `~/workspace/ai-assets/jackhpark-trading-agent`
- Runner: `scripts/run-review.sh` → headless `claude -p` (review + reconcile + commit/push)

## Daily flow

1. **Auto (13:30 PT):** the job writes `logs/reviews/<date>.md` (buy/sell proposals) and
   `logs/reconcile/<date>.md` (sheet drift), then commits + pushes. Read-only.
2. **You:** read the report (in the repo or on GitHub).
3. **To act:** in an interactive session during market hours (PT 06:30–13:00), ask the
   agent to execute — it recomputes live, runs `review_equity_order`, you confirm, it places
   and logs to `logs/trades/<date>.md`.

## Installing / moving the job to a host

Run **on the target machine, logged in as the owning user** (the GUI session must be active —
see the Keychain note below):

```bash
git clone git@github.com:jack-h-park/trading-agent.git \
  ~/workspace/ai-assets/jackhpark-trading-agent
cd ~/workspace/ai-assets/jackhpark-trading-agent

# 1. Claude Code installed and logged into the SAME claude.ai account
#    (so the Robinhood + Google Drive connectors are present):
claude            # complete login if prompted, then /exit

# 2. Install + load the LaunchAgent (generates the plist with this machine's paths):
bash scripts/install-launchd.sh
```

The installer warns if the host is not on Pacific time (the 13:30 schedule assumes PT).

## Verifying it works

**Do NOT verify over plain ssh** — see the Keychain note. Verify in the GUI session via
launchd:

```bash
# Trigger a run now (runs in the GUI session → Keychain/connectors available).
# NOTE: run-review.sh skips on weekends unless FORCE=1; launchctl can't pass env,
# so for a weekend smoke-test use the temp-agent method below.
launchctl kickstart -k gui/$(id -u)/com.jackpark.trading-agent-review
sleep 2
tail -f ~/workspace/ai-assets/jackhpark-trading-agent/logs/cron/$(date +%F).run.log
# success looks like:  run-review start … / run-review end rc=0 …
```

Then check outputs:

```bash
cd ~/workspace/ai-assets/jackhpark-trading-agent
ls -la logs/reviews/$(date +%F).md logs/reconcile/$(date +%F).md
git log --oneline -2
```

**Weekend / forced smoke-test** (bypasses the weekday guard, still in GUI context):

```bash
LBL=com.jackpark.trading-agent-review-test
REPO=~/workspace/ai-assets/jackhpark-trading-agent
cat > ~/Library/LaunchAgents/$LBL.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LBL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/scripts/run-review.sh</string></array>
  <key>EnvironmentVariables</key><dict><key>FORCE</key><string>1</string></dict>
  <key>RunAtLoad</key><false/>
</dict></plist>
EOF
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/$LBL.plist
launchctl kickstart -k gui/$(id -u)/$LBL
# …wait ~4 min, watch logs/cron/$(date +%F).run.log…
launchctl bootout gui/$(id -u)/$LBL && rm ~/Library/LaunchAgents/$LBL.plist
```

## Stopping / removing the job

```bash
launchctl bootout gui/$(id -u)/com.jackpark.trading-agent-review
rm ~/Library/LaunchAgents/com.jackpark.trading-agent-review.plist
```

Run this on any machine that should **stop** hosting the job (e.g. after moving it) so only
one machine runs it — duplicate hosts just double-commit.

## Manual run (interactive, ad-hoc)

From the repo on a machine with the GUI session active:

```bash
FORCE=1 bash scripts/run-review.sh    # bypasses the weekend guard
cat logs/cron/$(date +%F).run.log
```

(Over plain ssh this fails with "Not logged in" — Keychain note below.)

## Troubleshooting

### "Not logged in · Please run /login" when running over ssh
Expected. Claude stores its OAuth token in the macOS **login Keychain**, which is only
accessible from the **GUI login session** (where launchd LaunchAgents run), not from a
non-interactive ssh shell. The cron is fine; just verify via `launchctl kickstart`, not ssh.
The user metadata in `~/.claude.json` (`oauthAccount`) existing is **not** proof the token
is reachable headlessly — only the GUI session has it.

### Run fails / report not written
1. `tail -50 logs/cron/<date>.run.log` and `logs/cron/launchd.err.log`.
2. claude.ai session may have expired → on the host's GUI terminal run `claude`, re-auth.
3. Connector dropped → `claude mcp list` won't show claude.ai connectors (they're runtime-
   injected); confirm by a GUI `launchctl kickstart` test, check the run log for tool errors.

### Push rejected / commits not on GitHub
`run-review.sh` does `git pull --rebase --autostash` at start, so external commits normally
fast-forward. If a run still couldn't push (network), the commit stays local; the next run
pulls and pushes it. Check `git status -sb` and `git log origin/main..main`.

### iMac was asleep at 13:30
launchd runs the missed calendar job right after the machine next wakes (calendar jobs
aren't lost). To hit 13:30 exactly, keep the iMac awake (Energy Saver) or schedule a wake.

## Placing a live trade (human-in-the-loop)

Live orders never run from the cron. In an interactive session during regular hours:

1. Ask the agent to execute the latest proposal (or a specific order).
2. It recomputes signals on live prices (proposals can shift from the post-close report).
3. It calls `review_equity_order` and shows the preview + the compliance quote disclosure.
4. You confirm explicitly.
5. It calls `place_equity_order` and appends to `logs/trades/<date>.md`.

Per `config/guardrails.md`, every live order is confirm-before-place unless you have
explicitly enabled standing authorization (off by default).
