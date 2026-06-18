# Spreadsheet Import Policy

## Import stages

1. Upload
2. File hash stored
3. Parse
4. Validate columns and units
5. Stage changes
6. Generate diff
7. Human approve/reject
8. Apply migration
9. Snapshot old data
10. Rollback available

## Editable by spreadsheet

- product active/inactive state
- preferred product flag
- stock and pricing
- labour rules
- commercial notes
- feedback records
- review decisions

## Not silently editable by spreadsheet

- panel dimensions
- Voc/Isc/Vmp/Imp
- inverter electrical constraints
- wind-load rules
- structural approval status
- manufacturer constraints
