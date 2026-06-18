from app.core.compat import StrEnum
from pydantic import BaseModel, Field, HttpUrl

class ProductQualityLevel(StrEnum):
    Q0 = "Q0_scraped"
    Q1 = "Q1_datasheet_linked"
    Q2 = "Q2_parsed"
    Q3 = "Q3_reviewed"
    Q4 = "Q4_manufacturer_confirmed"
    QX = "QX_deprecated"

class ProductCategory(StrEnum):
    panel = "panel"
    inverter = "inverter"
    battery = "battery"
    mounting = "mounting"
    protection = "protection"
    cable = "cable"
    monitoring = "monitoring"
    other = "other"

class SpecProvenance(BaseModel):
    source_type: str = Field(..., examples=["manufacturer_datasheet", "nuvision_shop"])
    source_url: HttpUrl | None = None
    source_file_hash_sha256: str | None = None
    source_page: int | None = None
    source_text_quote: str | None = None
    extraction_method: str = Field(..., examples=["manual", "pdf_table", "ocr", "api"])
    confidence: float = Field(ge=0, le=1)
    review_status: str = "unreviewed"
    reviewed_by: str | None = None
    reviewed_at: str | None = None

class ProvenancedValue(BaseModel):
    value: str | int | float | bool | None
    unit: str | None = None
    normalized_value_si: float | None = None
    normalized_unit: str | None = None
    quality_level: ProductQualityLevel = ProductQualityLevel.Q0
    provenance: SpecProvenance

class ProductCreate(BaseModel):
    manufacturer: str
    manufacturer_model: str | None = None
    nuvision_sku: str | None = None
    category: ProductCategory
    title: str
    nuvision_url: HttpUrl | None = None
    status: str = "active"

class ProductRead(ProductCreate):
    product_id: str
    quality_level: ProductQualityLevel
