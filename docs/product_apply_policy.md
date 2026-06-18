# Product Apply Policy

## What can be applied

The apply workflow currently materialises:

- Products sheet -> current `products` table
- Datasheet_Review sheet with `review_status=reviewed` -> `product_specs` table

The following sheets are not yet materialised into live tables:

- Prices_Stock
- Labour_Rules
- Mounting_Rules
- Workflow_Feedback

They stay inside the approved ProductDataSnapshot until their dedicated tables and rules exist.

## Why this matters

Do not apply commercial/pricing/mounting rules casually. They affect quotes, install assumptions, and eventually liability. Each needs its own table and validation gate.

## Design-ready protection

The system refuses to mark a panel design-ready unless the reviewed critical electrical and physical fields exist. A panel can be active and visible but still not design-ready.

## No old evidence mutation

The current Product record can change over time. Old snapshots do not. Future quotes/calculation packets must reference the snapshot they used.
