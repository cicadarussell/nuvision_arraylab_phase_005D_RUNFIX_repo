# Data Quality Policy

Every product field has its own quality level.

| Level | Name | Meaning | Allowed use |
|---|---|---|---|
| Q0 | scraped | NuVision/shop text only | display/search |
| Q1 | datasheet_linked | manufacturer PDF found | queue/review |
| Q2 | parsed | machine parsed | preliminary |
| Q3 | reviewed | human checked against datasheet | design/BOM |
| Q4 | manufacturer_confirmed | API/direct manufacturer verified | final quote candidate |
| QX | deprecated | superseded/bad/discontinued | block new designs |

## Required provenance fields

- source type
- source URL
- file hash
- source page
- source text quote
- extraction method
- confidence
- review status
- reviewer
- review timestamp
- valid from/to
- supersedes field ID
