# Provider capabilities: Robinhood

Source: https://robinhood.com/us/en/support/articles/trading-with-your-agent/

## Supported

- **Long equities orders** (buy).
- **Options orders** — rolling out; may not be available on every account yet.
- Read-only: accounts, portfolio, positions, quotes, historicals, tradability, orders.
- Pre-trade simulation via `review_equity_order` (returns warnings).
- Watchlist management.

## Not supported

- **Short positions.**
- **Other asset classes**: crypto, futures, etc.
- Operating without a connected user account.

## Order spec support

| Field | Supported values |
|---|---|
| `side` | `buy` (long); `sell` to close longs |
| `asset_class` | `equity`; `option` (where enabled) |
| `order_type` | `market`, `limit` |

## Disclosed risk

The user is ultimately responsible for trades the agent places. The agent may misinterpret
instructions or act on incomplete data; Robinhood does not guarantee accuracy or
suitability. → default to confirm-before-place (`config/guardrails.md`).
