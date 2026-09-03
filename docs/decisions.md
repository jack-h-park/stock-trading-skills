# Decision log

Key choices and their rationale, newest-relevant first. Lightweight ADR style.

## 1. New dedicated repo, not the shared ai-skills library
A live, stateful trading agent (account config, logs, cron) is operational/project-specific.
The existing `jackhpark-ai-skills` repo is explicitly a *reusable, repo-agnostic* playbook
library and its README forbids project-specific commands/logs. Putting the agent there would
violate that charter. → Created `jackhpark-stock-trading-skills` under `~/workspace/ai-assets/`
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

## 13. Route by whether a reply is needed, not by which account a line concerns
The review produces two messages a day and they had drifted into saying the same things.
The afternoon Telegram digest opened with the Korean line the morning briefing had already
sent verbatim, and carried a `📰 Briefing note` repeating that morning's act-now item at
greater length — on 2026-08-11, the TQQQ/CPI decision, twice on one channel. Meanwhile the
reconcile alert, the one line in the review that asks Jack to change something, sat in the
message headed "read-only context".

The rule the fleet already uses is: **Telegram is awareness, Discord is where a response is
needed.** Applied here that means the channel is chosen by whether Jack has to act, not by
which account the line is about — so a holdings-sheet drift belongs with the Agentic
proposals even though it concerns other accounts, and a briefing item he has already read
belongs nowhere.

- Reconcile **drift** → Agentic/Discord message, with what to change in the sheet.
- Reconcile **match** → one line in the reference message; "no drift" is the expected state.
- Briefing note → removed. The morning briefing sends its own act-now on the same channel.
- Korean line → removed from the digest. What this message uniquely carries is the
  cross-account US total, which the briefing does not: that one is equity-only, priced at a
  different close, and silent on cash and crypto.

Not addressed here: the Discord side is still send-only. The gateway has run with one
platform since 2026-06-13, so "confirm in the interactive session" has nowhere to land on
Discord — see the note in operations.md.


## 14. Averaging down is a standing budget, not a count of adds
`strategy/policy.md` said "at most 2 total $100 adds per symbol **before an exit**" and never
defined whether a partial trim was that exit. It mattered: take-profit and TRIM both sell 50%,
so the question came up constantly, and the review was flagging its own guess every session
rather than following a rule — proposing REDISTRIBUTE → MSFT $65 / NVDA $65 / AMZN $65 under
one reading while noting it would be MSFT $98 / NVDA $98 under the other.

Both readings it was choosing between are wrong in a way the replacement is not:

- **Partial trim resets the count.** Take-profit fires only at +12% above average cost, so the
  reset arrives only when a position is WINNING — while the rule exists to pace one that is
  LOSING. A single temporary rally clears it, and a name can absorb $200 more after every
  bounce.
- **Partial trim counts for nothing.** The allowance becomes a LIFETIME budget: two adds and
  the symbol is closed to new capital until fully liquidated, however much was later sold at a
  profit and however long ago.

→ **$200 of added capital per symbol at any one time.** An add commits its dollar amount; a
sale releases that fraction of the committed figure; a full exit releases all of it. The
exposure bound is the same $200 at every moment as the lifetime reading, and the capital a
profitable trim released can go back to work. In practice both exit sizes are 50%, so it reads:
a half-sale buys back one add.

It also answers what a count could not. REDISTRIBUTE buys are not $100 — $65 in the sessions
that raised this — and "is a $65 buy one add?" had no answer. It commits $65.

Rounded to cents at every step, which is part of the rule rather than an implementation
detail: the 2026-08-12 take-profit sold 0.424941 of 0.849883 shares — 49.9999412%, not half —
and at full precision that leaves $100.00011766 committed and refuses the next $100 add by a
hundredth of a cent. Found by replaying the rule over the real fills before shipping it.

Computed per session by replaying `get_equity_orders` (live, not the CSV export), so there is no
new state to keep. If that history does not reach the position's opening the figure is not
trustworthy: the review says so and treats the symbol as capped, which is the safe direction.
## 15. Unlogged trades: detect them, don't reconstruct them
Between 2026-06-23 and 2026-08-13 the Agentic account filled eleven orders and `logs/trades/`
stayed empty. Two separate questions came out of that, and they have opposite answers.

**Back-fill the log from the broker? No.** The orders are fully recoverable from
`get_equity_orders`, so it was tempting. But `logs/` is a decision journal (decision #9,
architecture.md) and a reconstruction from broker data is entirely *what*: no review warnings,
no confirmation, no policy rule, no reason that symbol on that day. Written in the format of
`skills/log/SKILL.md` it would look like a decision record and be read as one, which is worse
than an empty directory — an empty directory is honestly empty. It would also stand a second
copy of these trades beside the Observatory database, which `docs/data-sources.md` argues
against for this exact account. The eleven are recorded once, as prose, in
the backlog note kept with the logs (`logs/trades/README.md`, untracked), including what the fill data does and does not recover and where on
imac-hermes the rest might still be found. It recovers more than expected — the eleven orders
were five sessions, and the AMZN sale matches the take-profit rule exactly (average cost
$235.3260, sold at +13.46% past the +12% trigger, quantity 0.849883/2 floored to six decimals
= the 0.424941 actually sold, $13.46 realized). That is the *mechanism*, and it is recoverable
only because the policy is written down. The judgment is not: which symbols signalled and how
they ranked, what `review_order` warned about, what was weighed before confirming, what was
proposed and declined. The README marks the inferred half as inferred, which a back-filled log
entry could not have done.

**Enforce the contract going forward? Yes, by detection.** The gap is structural, not
careless. `scripts/run-review.sh` always logs because the script makes logging a step; orders
are placed in interactive sessions where "review → confirm → place → log" is a written
instruction in `skills/trade/SKILL.md` and nothing checks it. Adding more emphasis to the
skill would not have changed the outcome — the instruction was already there and already
clear. What is available is that the daily review already queries Robinhood read-only, so it
can hold filled Agentic orders against `logs/trades/` and say when one is missing. That runs
whether or not anyone remembers, which is the property the contract lacked.

Placed in job B (reconcile) rather than a new job: it is bookkeeping drift between two records
of the same trades, which is what B already is, and B already has `get_equity_orders`
whitelisted. Parameters — scope, 30-day lookback, floor date, matching rule — in
`config/trade-log-check.md`. A floor date of 2026-08-24 keeps the eleven known misses out of
the daily alert; a warning that fires every day is one that stops being read.

**Agentic stays out of the *sheet* reconcile.** Its sheet section is agent-managed and
expected to diverge, so reconciling it would manufacture permanent noise. But the wording
"Agentic is out of scope for this reconcile" was doing more work than it should: the one
account this repo trades was reading as the one account nothing checked. It now says which
reconcile it is out of scope for, and names the trade-log check that covers it.
## 16. The approval bottleneck is a missing execution path, not a missing permission

**2026-08-31.** Every proposal the scheduled review produces waits for Jack to open an
interactive session, including the ones no human judgement went into. Asked to reduce
that friction, the obvious answer was to write a narrow standing authorization.

It already exists. `config/guardrails.md` has carried one since the guardrails were
written: a `buy`, in the universe, ≤ $100 notional, inside the daily and weekly counts,
notional market order, regular hours, ultra-liquid, kill-switch clear — placeable with
no per-order confirmation. Sells and every exit are always confirm. That is close to
exactly the envelope anyone would design today.

**What is missing is a path, not a permission.** The authorization is written for the
interactive agent, which holds `place_equity_order`. The scheduled review does not:
`scripts/run-review.sh` grants read tools only, on purpose, so that a cron job cannot
trade. So the standing authorization has never fired from the scheduled path, and the
review that computes a fully determined $100 buy at 13:30 has no way to act on it.

Recording this because the fix that suggests itself — "write an auto-execute policy" —
would have added a second, overlapping envelope and left the actual gap untouched. The
question to answer is narrower: should the scheduled path be given the order tool under
the authorization that already governs the interactive one, and what has to be true of
the data before it is.

Two preconditions were added to that authorization here, both of which
confirm-before-place had been covering silently:

- **One symbol, one direction** (`strategy/policy.md`). Entry fires at ≥5% below the
  20-day high and TRIM at ≥10%, so every TRIM candidate is also a BUY candidate. On
  2026-08-31 GOOGL sat at −11.75% and the review carried both readings in one message.
  Only the committed-add cap kept it from proposing a buy and a sell of the same name
  on the same morning. A person reading that would have noticed; a rule executing it
  would not.

  The first draft of this entry resolved it with a default — the exit wins, because it
  reduces exposure. That reads as principled and is still probably the right answer,
  but it is a trading decision wearing a tie-break's clothes, and the rules genuinely
  do not determine it: the entry rule sees a name worth adding to, the exit rule sees
  one worth cutting, and both are working correctly. **So the review asks instead.**
  Neither side is auto-eligible until Jack rules; the ruling is stored in
  `config/rulings.json`, applied silently thereafter, and never asked twice — the
  fractional-sell question was asked on two consecutive days in July for want of
  exactly that, and its answer became a rule in `config/trim-policy.md`. Three
  identical answers across different symbols means the rule should be written into
  policy and the entries retired: the store is a waiting room, not a home.

  This is the shape the whole plan turns on. Automating what the rules determine only
  pays if the cases they do not determine are visibly escalated rather than quietly
  resolved — otherwise autonomy is bought by making judgement calls invisible, which
  is the opposite of the trade being made here.
- **Price freshness.** Prices come from a table written by a scheduled refresh and
  re-checked by nothing. Within one week it held a three-day-frozen figure (a refresh
  failing at `extract:fx-ledger`) and an intraday quote labelled as a settled close
  (MSFT 509.71 against 513.53). Both were visible only afterwards. A person sees the
  number before approving; an automated placement does not.

Both are fixed as of this entry, and neither is a reason to wait — they are the reason
the envelope needed stating before anything is switched on.

**Not decided here:** whether the scheduled path gets the order tool at all. That is a
live-money change and Jack's to make. The next step is to run the decision offline —
compute what would have been placed, then check the following session whether it still
held — so the question is answered from a record rather than an argument.
## 17. Robinhood MCP error-log triage (2026-08-31)

Four recurring error shapes in the trader profile's `gateway.error.log` were flagged
while fixing the PR #595 path-rename bug and investigated separately, since none of
them were that bug. Findings, so a future sighting isn't re-diagnosed from scratch:

- **Keepalive timeouts** (`MCP server 'robinhood' keepalive failed ... Errno 60
  Operation timed out`) — ongoing, ~2/day across the log's 13 timestamped days, plus
  more in its untimestamped backlog. Every sampled occurrence lines up with a
  simultaneous Telegram connection failure in the same log window — two unrelated
  external services failing together points to local network flakiness on the iMac,
  not `agent.robinhood.com` being slow or down. Each failure self-heals via reconnect.
  Not currently actionable; not investigated further.
- **"We're required to have you answer some questions about your investing goals..."**
  (`place_equity_order`, 2 occurrences) and **`FORBIDDEN: agent not authorized to
  access this account`** (`review_equity_order`, 2 occurrences). Both are confined to
  the log's untimestamped section, which predates the file's 2026-08-18 17:31
  timestamp cutover — i.e. both happened during the Agentic account's first week (the
  token store was created 2026-08-12). Zero recurrences since; real trades filled
  successfully starting 2026-08-25 (AMZN/NVDA/MSFT — see `logs/trades/2026-08-25.md`).
  The FORBIDDEN errors plausibly trace to the unlocked, non-atomic token-file writes
  documented in `scripts/robinhood_token.py`'s module docstring, fixed by #31
  (`55dd713`, merged 2026-08-19) — two racing writers could leave a torn or stale
  access token in place. The investing-goals block reads as a one-time Robinhood
  onboarding gate on the newly created account, since cleared on Robinhood's side.
- **resource name `"flatline:server-combined.flatline.region.rh:443"` of type
  Cluster not found** (gRPC authorization-check error, 1 occurrence, in the same
  untimestamped/early window as the two above). Reads as a Robinhood-side backend
  service-discovery hiccup — outside anything our proxy or token logic touches.
  Never repeated.

None of the four blocked a real trading-review run at any point the log or cron
history covers. No code change made here.

## 17. Thresholds are defaults a departure may read past, not rules to keep adding to

**2026-09-02.** §16 escalated what the rules could not determine, and the first
thing it escalated was a buy-vs-exit conflict on GOOGL. Jack's reply was the
useful part: *do I really have to set every one of these deterministically?*

He was right, and the pattern is visible in the record. Entry fires at ≥5% below
the 20-day high and TRIM at ≥10% below the same high, so the two must collide —
and every fix on offer was another number. The number proposed to settle it was
30%. Working it out afterwards: with TRIM at 30%, the −8% stop-loss from average
cost fires first on **all six** open positions, by 5 to 18% of price, so 30% would
have quietly retired TRIM for every single name in the universe and left it live
only for the two ETFs, which have no stop. Nobody saw that from the number. It
took arithmetic done after the fact.

A number is also a lossy container. What Jack actually said was that he reads a
drawdown as an opportunity — a stance that generalises. "30%" is that stance
encoded so it happens to be right for GOOGL and wrong for VOO.

**So the thresholds become defaults.** The review may read past one when the
situation says otherwise, provided it names the threshold, the number, what it did
instead, and why. Every departure is recorded in the decision record and carried
into the shadow ledger.

Three things keep this from being a licence:

- **The guardrails are not departable.** Order notional, the daily and weekly
  counts, the position cap, the committed-add cap, the kill switch, regular hours,
  the universe. Those bound how wrong a judgement can be; a judgement that can
  widen its own bounds is not bounded. A departure needing one of them to move is
  a proposal to change the guardrail, and goes to Jack as that.
- **A departed item is never auto-eligible.** The standing authorization covers
  what the rules fully determine. A departure is by definition not that.
- **Departures do not accumulate into permission.** Ten on TRIM do not make the
  eleventh automatic. They make the case for editing the number, which Jack does.

**What is given up, stated plainly:** `shadow-check.py` can verify that the price
a decision used was real. It cannot verify that reasoning was sound. Moving the
signal layer from arithmetic to judgement gives up a kind of checkability, and the
mitigation — every departure carries its reasoning, and the ledger accumulates
them — is weaker than re-deriving a number. It is a real trade and it was made
knowingly.

The bet is that the ledger answers the question the thresholds could not: which
number is wrong, in which direction, how often. A threshold departed from
repeatedly and consistently is set wrong; one never departed from is working.
That is a better instrument than another round of picking numbers, and it is
reversible — the thresholds are still there, and the departures are all on record.

§16's ruling mechanism survives for what it was actually for: situations the rules
cannot anticipate and the agent should not decide alone — a pending acquisition, a
halt, an earnings gap that makes the 20-day high meaningless. A threshold overlap
was never one of those.
