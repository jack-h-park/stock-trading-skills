---
name: log
description: >
  Markdown logging format contract for the stock-trading-skills. Use when writing trade logs or
  review reports so every entry is consistent, append-only, and multi-broker friendly.
---

# Skill: log

All logs are **append-only markdown**, one file per day, never overwritten.

- Orders / cancels → `logs/trades/YYYY-MM-DD.md`
- Review reports → `logs/reviews/YYYY-MM-DD.md`

If the day's file exists, append a new timestamped section.

## Front matter (top of each file)

```yaml
---
date: YYYY-MM-DD
broker: robinhood        # provider key; enables multi-broker aggregation later
account: <account-id>
---
```

## Trade entry format

```markdown
## HH:MM:SS — BUY 5 AAPL @ limit 180.00

- broker: robinhood
- account: <id>
- side: buy | asset_class: equity | tif: gfd
- review: est. cost $900.00; warnings: [none | ...]
- confirmation: user-approved | standing-auth (rule: <which>)
- result: order_id <id>, status <accepted|filled|...>
- policy ref: strategy/policy.md#<rule>
```

For a cancel, use `## HH:MM:SS — CANCEL <order_id> AAPL` and note the reason.

## Review entry format

Per `skills/portfolio-review/SKILL.md`: snapshot, positions table, flags, proposed actions.

## Rules

- Never edit or delete past entries — corrections go in a new entry referencing the old one.
- Always include the `broker:` field so logs stay aggregatable across providers.
- Record the *review warnings* verbatim, not just the final action.
