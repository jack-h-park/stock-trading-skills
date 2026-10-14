# Observatory database glossary

For the questions no CLI answers. Prefer a CLI whenever one exists.

## Opening it

- Path: `$HOME/workspace/data/stock-management/outputs/stock-portfolio-observatory/stock-portfolio-observatory.db`
  (the `dbPath` in any CLI's provenance is authoritative).
- **Read-only, always:** `sqlite3 -readonly "$DB"`, or in Python
  `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`.
- **Not during a refresh.** Ingest deletes the file and rebuilds it at the same path. If
  `$OBS/.refresh.lock` exists, stop and say a refresh is running.
- Say the DB's build time (`dbBuiltAt` from a CLI, or the file mtime) with any figure.

## Views and tables

`tax_lots`, `realized_lots`, `transactions`, `holdings`, `dividends` are **views** over
`*_all` tables, filtered to `account_wrapper in ('taxable', 'isa') and asset_class =
'security'` — taxable securities. Pensions, IRP and gold are only in the `*_all` tables.
For a tax question, the views are what you want.

## Columns that matter

| Column | Tables | Meaning |
|---|---|---|
| `account` | all | The Observatory's account label (an alias). Never a number |
| `brokerage`, `market` | all | Broker; `US` or `KR` market |
| `acquired_date` | tax_lots, realized_lots | Lot acquisition date |
| `open_quantity` | tax_lots | Shares still held in the lot |
| `acquisition_cost_usd` | tax_lots, realized_lots | The dollar cost the IRS measures against. A won lot is converted at the acquisition-date rate. Null where no rate reaches back |
| `acquisition_cost_krw` | tax_lots, realized_lots | The won cost Korea taxes against: a foreign lot at the USD/KRW rate on its **acquisition date** |
| `cost_basis_krw` | tax_lots, realized_lots | Won cost at **today's** rate, for display. **Never use it for tax**: it differs from `acquisition_cost_krw` by the FX move since purchase |
| `tax_term` | tax_lots, realized_lots | `Long-term` or `Short-term` (US rule: held more than 365 days) |
| `holding_days` | tax_lots, realized_lots | Days held |
| `account_wrapper` | all `*_all` | `taxable`, `isa`, `irp`, pension types. Only taxable/isa are in the views |
| `sold_date`, `quantity_sold` | realized_lots | The sale |
| `realized_gl_krw`, `native_realized_gl` | realized_lots | Gain in won, and in the lot's own currency |
| `basis` | realized_lots | `replay` (the Observatory's own FIFO walk; available year-round) or `1099b` (the broker's filed figure, wash sales and return-of-capital adjustments applied). They differ by construction; say which |
| `superseded_by` | realized_lots | Set on a replay row a filing replaced. Filter `superseded_by is null` to avoid double-counting |
| `tax_year` | realized_lots | Tax year of the sale |
| `placed_agent` | transactions | `drip`, `recurring`, `user` where the broker says. A DRIP buy inside a loss window washes part of the loss |
| `price_date`, `rate` | historical_fx_rates | USD/KRW by date |

## Year-to-date figures: policy vs computed

- **Policy** (what the owner entered): `$OBS/data/tax-policy.json`, the year-to-date
  realized fields. Read it; never write it.
- **Computed** (what the data makes it): `select value from meta where key in
  ('us_ytd_realized_computed', 'kr_ytd_realized_computed')`. JSON with `taxYear`,
  `basis`, `shortUsd`, `longUsd`, `byMarket`, `scope` (`worldwide`).
- Ingest's validation check `us_ytd_realized_assumption_reviewed` fails when the two
  differ by more than $1.

Report both values and the difference, and point the owner to `/tax-settings` on the
Observatory to apply a change. The computed figure is deliberately not wired into the
policy: where a broker applied a basis adjustment, the replay and the filed figure
genuinely differ, and the owner decides which to enter.
