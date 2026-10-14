# Presentation

## Order

1. **Verdict line.** One sentence: what changed, or the scenario's headline difference.
   A fact, not advice: "Selling only the long-term lots now is estimated at $X less tax
   than selling everything now, under the stated inputs" — never "you should".
2. **Compact table.** Only the rows that answer the question; the full table stays in the
   JSON/MD file and the reply names its path.
3. **Provenance lines**, quoted from the command's output, not paraphrased.
4. **`<<<KO>>>` block.** A Korean copy of the verdict line and of the table's column
   labels. Numbers, tickers and dates are shared by both versions and are not restated.

## Example (synthetic tickers and figures)

```
Under the stated inputs, selling only the long-term lots now is estimated at $1,240 less US tax than selling everything now.

| Scenario | Lots | Gain (USD) | US tax (USD) |
|---|---|---|---|
| lt-now   | 3 | 4,100 | 615 |
| sell-all | 7 | 9,800 | 1,855 |

DB built 2026-01-05T22:01Z · policy sha256 1a2b… (mtime 2026-01-02) · prices: db as of 2026-01-05 · FX latest 2026-01-05 · credit mode estimated-us-source
Estimate under the stated inputs; not tax advice.

<<<KO>>>
주어진 입력 기준으로, 장기 보유 로트만 지금 매도하는 경우가 전량 매도보다 미국 세금이 적게 추정됩니다.
| 시나리오 | 로트 수 | 차익 (USD) | 미국 세금 (USD) |
```

## Things that must be said when true

- A source was unavailable (`sources.*.status == "unavailable"`): name it and what the
  answer cannot see because of it.
- A window was reported by only one source: Robinhood cannot see other brokers, the
  Observatory cannot see anything not yet downloaded and refreshed.
- A figure's `basis` is `replay` rather than `1099b`.
- A future sale date uses the latest available FX rate, not the sale-date rate.
- A broker-only fill is expected lag, not an error, unless it is marked `overdue`.

## Routing

Something the owner must decide — a sale plan, a policy difference to apply in
`/tax-settings` — belongs on Discord, in the trader channel. Awareness (the daily
tax-calendar change notice) goes to the trader Telegram topic. An interactive answer goes
back to the surface the owner asked on.
