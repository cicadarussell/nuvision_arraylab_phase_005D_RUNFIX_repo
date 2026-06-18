# Phase 002C, Datasheet Review UI + Model Selection Hardening

## Reason for this phase

Phase 002B could detect multi-model datasheet conflicts, but a real reviewer workflow needs stricter model selection and clearer UI/API evidence. Solar datasheets often contain multiple wattage variants in one PDF. Assigning the wrong row to a product would corrupt inverter stringing and panel packing later.

## Engineering rule

A conflicted candidate requires:

- corrected numeric value,
- corrected unit,
- selected manufacturer model or datasheet variant,
- model-selection basis,
- reviewer reason,
- named reviewer.

No batch approval for conflicted candidates.

## Debugging value

The new review queue V2 groups candidates by product and datasheet, so debugging starts from the product evidence packet rather than isolated values. Table preview and OCR status endpoints make extraction failure visible.

## Design-readiness value

Panel design readiness is recalculated from ProductSpec rows, not shop text. This prepares the later panel-packing and inverter/string engines to trust only reviewed specs.
