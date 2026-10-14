# CLI catalogue

Every command here is read-only. The Observatory CLIs belong to
stock-portfolio-observatory; the scripts belong to this repo.

## Observatory CLIs — common contract

Run through `python3 $REPO/scripts/observatory_cli.py <name> [args]`, which runs
`pnpm --silent <name> [args] --json` in `$OBS` with the same PATH the Observatory's cron
wrappers use. Direct form, if you need it: `cd $OBS && pnpm <name> [args] --json`.

| Exit | Meaning | What to say |
|---|---|---|
| 0 | One JSON object, `"ok": true` | Answer from it |
| 2 | Usage error | Fix the arguments; do not guess a flag |
| 3 | A refresh holds `.refresh.lock`; the DB is being rebuilt | "The Observatory is refreshing; retry in a few minutes." Do not read the DB meanwhile |
| 1 | Anything else; `{"ok": false, "error": ...}` | Quote the error |

Every success object carries `provenance`: `dbPath`, `dbBuiltAt`, `dbMtime`,
`policyPath`, `policySha256`, `policyMtime`, `priceSource` (`db` or `file:<name>`),
`pricesAsOf`, `fxDates.latest`, `krCreditMode`, `disclaimer`. Quote these.

Accounts appear only as the Observatory's labels (aliases). Never print an account
number.

### `tax:scenario`

```
--scenario lt-now|wait-lt|sell-all|harvest|filing-compare|date-shift
[--as-of YYYY-MM-DD]      default today (Pacific)
[--sell-date YYYY-MM-DD]  required for date-shift
[--tickers A,B]           restrict to these US tickers
[--filing us-only|us-kr]  default: the policy's filing profile for the tax year
[--prices <file.json>]    {"asOf": "<ISO>", "quotes": {"TICKER": <lastPriceUsd>}}
```

| Scenario | Answers |
|---|---|
| `lt-now` | Sell only the lots already long-term |
| `wait-lt` | Sell each lot on the day it turns long-term (date per lot) |
| `sell-all` | Sell everything now |
| `harvest` | Per ticker, loss available and tax saved per $ of loss, as a frontier |
| `filing-compare` | The same sales under US-only and US+KR filing |
| `date-shift` | The same sales on `--as-of` vs `--sell-date` |

Output adds `scenario`, `inputs`, `sales[]` (ticker, account, acquiredDate, quantity,
basisUsd, proceedsUsd, gainUsd, term, becomesLongTermOn, gainKrw), `estimate` (federalUsd,
niitUsd, stateUsd, usTotalUsd, krTaxKrw, krCreditUsd, totalUsd), `comparison[]`,
`frontier[]` (harvest), `lowestEstimate` ("lowest estimated tax under the stated inputs").

Currency rule, held in the CLI: US gains in USD; KR gains in KRW at the lot's
acquisition-date FX and the sale-date FX (latest available for a future date, and the
output says so).

### `tax:calendar`

`[--as-of YYYY-MM-DD]`. Adds `washSaleWindows[]` (ticker, soldDate, blockedThrough,
daysLeft, realizedLossUsd, accounts, boughtSinceSale, dripActive), `drip[]`, `settlement`
(`status` is `unavailable` until the account map marks cash accounts), `coverage`
(brokerages, excludes). You normally read it through `tax_calendar.py`, not directly.

### `tax:realized`

`--since YYYY-MM-DD [--until YYYY-MM-DD]`. Adds `rows[]` (account, brokerage, ticker,
soldDate, quantity, proceedsUsd, gainUsd, term, basis). US securities only. `basis` is
`replay` (the Observatory's own FIFO walk) or `1099b` (the broker's filed figure, wash
sales applied); say which.

### `sources:status`

Adds `lastPush` (`at`, `ageHours`: the last laptop → host push), `sources[]` (folder,
latestFile, latestMtime), `due[]` (what the coverage check says is missing).

### `deploy/hermes/refresh-now.sh` (owner request only)

`$OBS/deploy/hermes/refresh-now.sh`. Rebuilds the database and republishes the Google
Sheets. Prints the `Validation: N/M` line, failing check ids, any `[source] MISSING`
lines and `rc=<n>`. Exit 3 with `Refresh already running` when the lock is held.

## Scripts in this repo

All reach Robinhood through `scripts/mcp-robinhood-proxy.py --read-only`; none can place
an order. Python 3.9 compatible.

### `scripts/tax_calendar.py`

`[--date YYYY-MM-DD] [--print message|json|none] [--no-robinhood] [--no-observatory]`.
Writes `logs/tax-calendar/<date>.json` and `.md`. Joins tax_guard's cross-account
Robinhood loss sales, open and recently filled Robinhood orders, the Observatory's
`tax:calendar`, earnings dates for planned-sale tickers, and
`config/planned-sales.local.json`.

JSON: `sources` (robinhood / observatory / earnings, each `ok` or `unavailable` with a
reason), `windows[]` (ticker, firstSale, lastSale, blockedThrough, daysLeft, sales[] with
their source, `reportedBy`, dripActive, boughtSinceSale), `orderEvents[]` (effect:
`open-buy-inside-window`, `filled-buy-inside-window`, `open-buy`, `open-sell`,
`recent-buy`), `plannedSales[]` (window, conflicts, earningsInWindow, notes),
`plannedSalesProblems`, `fingerprint`.

`reportedBy` is who reported a window. Robinhood sees only its three accounts; the
Observatory sees every broker but only after a statement is downloaded and refreshed. A
window reported by one source only is normal; an unavailable source means the list may
be short — say so.

### `scripts/fill_reconcile.py`

`--since YYYY-MM-DD [--until D] [--lag-days 7] [--format md|json]`. Robinhood sell fills
vs `tax:realized`, matched on role → Observatory label, ticker, US/Eastern trade date
(±1 day, marked) and quantity. Three lists: `matched`, `brokerOnly` (expected for a few
days after a sale — the statement is not downloaded and refreshed yet; `overdue` past
`--lag-days`), `observatoryOnlyOrMismatch` (a real discrepancy). If either side was
unavailable, nothing is compared and the report says so.

### `scripts/analyst_facts.py`

See the `analyst-facts` skill.
