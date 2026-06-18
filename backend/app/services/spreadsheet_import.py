from dataclasses import dataclass
from typing import Any
import hashlib
import io
import pandas as pd

REQUIRED_SHEETS = {"Products", "Prices_Stock", "Labour_Rules", "Datasheet_Review", "Mounting_Rules", "Workflow_Feedback"}
REQUIRED_PRODUCT_COLUMNS = {"product_id", "nuvision_sku", "manufacturer", "manufacturer_model", "category", "status", "preferred", "notes"}

@dataclass
class SpreadsheetImportReport:
    file_hash_sha256: str
    sheet_names: list[str]
    missing_sheets: list[str]
    missing_product_columns: list[str]
    row_counts: dict[str, int]
    ok_for_staging: bool

def inspect_spreadsheet_bytes(data: bytes) -> SpreadsheetImportReport:
    file_hash = hashlib.sha256(data).hexdigest()
    workbook = pd.ExcelFile(io.BytesIO(data))
    sheet_names = workbook.sheet_names
    missing_sheets = sorted(REQUIRED_SHEETS - set(sheet_names))
    product_columns: set[str] = set()
    if "Products" in sheet_names:
        products = pd.read_excel(workbook, "Products", nrows=0)
        product_columns = set(map(str, products.columns))
    missing_product_columns = sorted(REQUIRED_PRODUCT_COLUMNS - product_columns)
    row_counts = {sheet: int(len(pd.read_excel(workbook, sheet))) for sheet in sheet_names}
    return SpreadsheetImportReport(file_hash, sheet_names, missing_sheets, missing_product_columns, row_counts, not missing_sheets and not missing_product_columns)

def build_diff_preview(old_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]], key: str) -> dict:
    old = {str(r.get(key)): r for r in old_rows if r.get(key) is not None}
    new = {str(r.get(key)): r for r in new_rows if r.get(key) is not None}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = []
    for k in sorted(set(old) & set(new)):
        changes = {}
        for field, new_value in new[k].items():
            old_value = old[k].get(field)
            if str(old_value) != str(new_value):
                changes[field] = {"old": old_value, "new": new_value}
        if changes:
            changed.append({"key": k, "changes": changes})
    return {"added": added, "removed": removed, "changed": changed, "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)}}
