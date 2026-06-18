# Commercial Snapshot Policy

## Why this exists

Prices, stock, and lead times change quickly. Engineering specs should change slowly and only with evidence. ArrayLab therefore stores commercial data separately from engineering product truth.

## Allowed fast changes

- stock status;
- lead time;
- trade/list price;
- supplier priority;
- product visibility/status, within allowed states.

## Blocked destructive actions

The following product statuses are not allowed in imports:

- `delete`
- `deleted`
- `remove`
- `removed`

Use `hidden`, `discontinued`, `replacement`, or `QX_deprecated` instead.

## Old quote protection

A quote copies price and stock payloads from current snapshots at creation time. Later price changes create new snapshot rows and do not mutate existing quote payloads.

## Rollback behaviour

Rollback is a marker and audit record. It does not erase history or delete rows. Restoration must be done as a new approved import/application.
