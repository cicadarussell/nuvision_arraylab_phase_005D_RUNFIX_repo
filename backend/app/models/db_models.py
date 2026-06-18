from __future__ import annotations

from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    nuvision_sku: Mapped[str | None] = mapped_column(String, nullable=True)
    manufacturer: Mapped[str] = mapped_column(String, nullable=False)
    manufacturer_model: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    quality_level: Mapped[str] = mapped_column(String, nullable=False, default="Q0_scraped")
    nuvision_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


    specs: Mapped[list["ProductSpec"]] = relationship(back_populates="product")


class ProductSpec(Base):
    __tablename__ = "product_specs"

    spec_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_value_si: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    quality_level: Mapped[str] = mapped_column(String, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="unreviewed")
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String, nullable=True)
    supersedes_spec_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="specs")


class CalculationRun(Base):
    __tablename__ = "calculation_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    software_version: Mapped[str] = mapped_column(String, nullable=False)
    engine_version: Mapped[str] = mapped_column(String, nullable=False)
    input_snapshot_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    output_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    product_data_snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assumption_set_id: Mapped[str | None] = mapped_column(String, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())




class YieldAssumptionSet(Base):
    """Versioned yield assumptions for preview/engineering calculations.

    Assumptions are stored separately from calculations so a quote can show exactly
    which loss factors, yield basis, and source notes were used. Hidden assumptions are
    spreadsheet termites, so they live here under bright, unpleasant light.
    """

    __tablename__ = "yield_assumption_sets"

    assumption_set_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    model_tier: Mapped[str] = mapped_column(String, nullable=False, default="T0_rough_kwh_per_kwp")
    specific_yield_kwh_per_kwp_year: Mapped[float] = mapped_column(Float, nullable=False)
    system_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=14.0)
    shade_loss_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    degradation_year1_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    albedo: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="preview_default")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())




class PvgisRequestCache(Base):
    """Backend-owned PVGIS request/response cache.

    Browsers must not call PVGIS directly from ArrayLab. Every PVGIS response used
    by yield preview is cached by request hash so the calculation packet can show
    exactly which location, tilt, azimuth, loss and response data was used.
    """

    __tablename__ = "pvgis_request_cache"

    request_hash_sha256: Mapped[str] = mapped_column(String, primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    adapter_version: Mapped[str] = mapped_column(String, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annual_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    parsed_monthly: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class PanelPackingOverrideRecord(Base):
    """Immutable human override record for panel-packing candidate selection.

    The calculation run remains the generated evidence. This table records the human
    decision to choose one of that run's already-selected/generated candidate layouts.
    It is append-only: later corrections create a new override row instead of mutating
    the old one, because apparently history should not be edited with a broom.
    """

    __tablename__ = "panel_packing_overrides"

    override_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    calculation_run_id: Mapped[str] = mapped_column(ForeignKey("calculation_runs.run_id"), nullable=False, index=True)
    selected_candidate_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    selected_candidate_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    selected_layout_export_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    intended_use: Mapped[str] = mapped_column(String, nullable=False, default="preview")
    reviewer: Mapped[str] = mapped_column(String, nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String, nullable=False)
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    override_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())



class SpreadsheetImport(Base):
    """Immutable import record for controlled spreadsheet ingestion.

    Approval creates a ProductDataSnapshot. Rows stay staged so that an audit can show
    exactly what came from the uploaded workbook.
    """

    __tablename__ = "spreadsheet_imports"

    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="uploaded")
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String, nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diff_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    staged_rows: Mapped[list["StagedImportRow"]] = relationship(back_populates="spreadsheet_import", cascade="all, delete-orphan")
    snapshots: Mapped[list["ProductDataSnapshot"]] = relationship(back_populates="spreadsheet_import")


class StagedImportRow(Base):
    __tablename__ = "staged_import_rows"
    __table_args__ = (UniqueConstraint("import_id", "sheet_name", "row_number", name="uq_staged_row_import_sheet_row"),)

    staged_row_id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("spreadsheet_imports.import_id"), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    spreadsheet_import: Mapped[SpreadsheetImport] = relationship(back_populates="staged_rows")


class ProductDataSnapshot(Base):
    """Versioned, immutable product-data snapshot created after import approval."""

    __tablename__ = "product_data_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    import_id: Mapped[str] = mapped_column(ForeignKey("spreadsheet_imports.import_id"), nullable=False)
    content_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    spreadsheet_import: Mapped[SpreadsheetImport] = relationship(back_populates="snapshots")


class ProductVersion(Base):
    """Immutable version row for every product materialisation.

    Product is the current index. ProductVersion is the audit trail. Old project
    records should point at product-data snapshots/calculation packets, not live Product.
    """

    __tablename__ = "product_versions"
    __table_args__ = (UniqueConstraint("product_id", "application_id", name="uq_product_version_product_application"),)

    version_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.product_id"), nullable=False)
    application_id: Mapped[str] = mapped_column(ForeignKey("product_snapshot_applications.application_id"), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("product_data_snapshots.snapshot_id"), nullable=False)
    source_row_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    product_payload_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    product_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductSnapshotApplication(Base):
    """Controlled application of an approved ProductDataSnapshot into current product tables.

    Previewing creates this record. Applying it updates current Products/ProductSpecs and
    writes immutable ProductVersion rows. The source snapshot remains frozen.
    """

    __tablename__ = "product_snapshot_applications"

    application_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("product_data_snapshots.snapshot_id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="previewed")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preview_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    diff_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceStockApplication(Base):
    """Controlled application of commercial price/stock rows from an approved snapshot.

    This is separate from ProductSnapshotApplication because commercial truth changes
    faster than engineering truth. Applying it creates new price/stock snapshot rows;
    it never edits old quote evidence.
    """

    __tablename__ = "price_stock_applications"

    application_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("product_data_snapshots.snapshot_id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="previewed")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preview_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    diff_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceSnapshot(Base):
    """Immutable price evidence row.

    Quote records must copy from these rows. Later price imports create newer rows but
    must not mutate old quote packets. Humans discovered accounting, then immediately
    needed evidence ledgers. Predictable species.
    """

    __tablename__ = "price_snapshots"

    price_snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("price_stock_applications.application_id"), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(ForeignKey("product_data_snapshots.snapshot_id"), nullable=False)
    source_row_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    trade_price_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    list_price_gbp: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String, nullable=False, default="GBP")
    payload_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockSnapshot(Base):
    """Immutable stock/lead-time evidence row."""

    __tablename__ = "stock_snapshots"

    stock_snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("price_stock_applications.application_id"), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(ForeignKey("product_data_snapshots.snapshot_id"), nullable=False)
    source_row_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    stock_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    stock_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier_priority: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommercialQuoteSnapshot(Base):
    """Immutable commercial quote packet.

    This is a deliberately boring evidence object. It stores copied price payloads so
    later price/stock updates cannot rewrite old quotes.
    """

    __tablename__ = "commercial_quote_snapshots"

    quote_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    quote_payload_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    quote_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    price_snapshot_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    stock_snapshot_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RollbackRecord(Base):
    """Non-destructive rollback marker.

    ArrayLab does not delete history. Rollback creates a record that points to a prior
    snapshot/application/version. Restoring is a new forward action, not a time machine.
    """

    __tablename__ = "rollback_records"

    rollback_id: Mapped[str] = mapped_column(String, primary_key=True)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    rollback_kind: Mapped[str] = mapped_column(String, nullable=False, default="non_destructive_marker")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    target_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasheetFile(Base):
    """Archived manufacturer datasheet evidence.

    The PDF/text hash is the authority for later candidate specs. Product shop text is not
    allowed to become engineering truth through this route. Cruel to spreadsheets, kind to roofs.
    """

    __tablename__ = "datasheet_files"

    datasheet_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manufacturer_datasheet")
    file_hash_sha256: Mapped[str] = mapped_column(String, nullable=False, index=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String, nullable=False, default="archived")
    extraction_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    uploaded_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasheetCandidateSpec(Base):
    """Machine-extracted candidate value from a datasheet.

    Candidate rows are Q2 at most. They are deliberately not design truth until a reviewer
    promotes them into ProductSpec as Q3_reviewed.
    """

    __tablename__ = "datasheet_candidate_specs"

    candidate_id: Mapped[str] = mapped_column(String, primary_key=True)
    datasheet_id: Mapped[str] = mapped_column(ForeignKey("datasheet_files.datasheet_id"), nullable=False, index=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    field_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_value_si: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String, nullable=False, default="regex_text")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_spec_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasheetReviewRecord(Base):
    """Immutable record of a human review action over a candidate spec."""

    __tablename__ = "datasheet_review_records"

    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("datasheet_candidate_specs.candidate_id"), nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    reviewer: Mapped[str] = mapped_column(String, nullable=False)
    corrected_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    corrected_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_spec_id: Mapped[str | None] = mapped_column(String, nullable=True)
    review_payload_hash_sha256: Mapped[str] = mapped_column(String, nullable=False)
    review_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasheetSourceDomain(Base):
    """Controlled list of accepted datasheet source domains.

    A datasheet URL is not automatically trusted just because it ends in .pdf. Humans, somehow,
    keep inventing marketing downloads. This table lets NuVision explicitly approve manufacturer
    or supplier domains before automated download jobs are accepted.
    """

    __tablename__ = "datasheet_source_domains"

    domain_id: Mapped[str] = mapped_column(String, primary_key=True)
    domain: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="approved")
    source_kind: Mapped[str] = mapped_column(String, nullable=False, default="manufacturer")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DatasheetDownloadJob(Base):
    """Network-independent download queue row.

    Phase 002B queues and validates jobs; a later worker can fetch bytes. We do not hide failed
    network work inside request handlers like a gremlin living in FastAPI.
    """

    __tablename__ = "datasheet_download_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    datasheet_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class DatasheetOcrJob(Base):
    """Explicit OCR queue row for scanned/no-text PDFs.

    OCR output is never promoted directly to design truth. It can only create Q2 candidates
    that still need the same Q3 human review as native-text extraction. Apparently even
    robots need adult supervision.
    """

    __tablename__ = "datasheet_ocr_jobs"

    ocr_job_id: Mapped[str] = mapped_column(String, primary_key=True)
    datasheet_id: Mapped[str] = mapped_column(ForeignKey("datasheet_files.datasheet_id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine: Mapped[str | None] = mapped_column(String, nullable=True)
    output_text_hash_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())



class ProjectRecord(Base):
    """Current project index.

    ProjectVersionSnapshot is the audit trail. Current rows are allowed to evolve;
    snapshots and calculation runs are the evidence chain. Because apparently projects
    should not silently change under old quotes. Radical stuff.
    """

    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    customer_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectVersionSnapshot(Base):
    """Immutable project geometry snapshot.

    Used before calculations so a mounting precheck/yield run can always be replayed
    from the exact site/roof/obstruction state used at the time.
    """

    __tablename__ = "project_version_snapshots"

    project_snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    snapshot_kind: Mapped[str] = mapped_column(String, nullable=False, default="geometry")
    snapshot_hash_sha256: Mapped[str] = mapped_column(String, nullable=False, index=True)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SiteRecord(Base):
    """Site record with explicit source/confidence fields.

    Postcode and coordinates are not sacred truth. Source and confidence travel with
    them so later survey/GIS corrections do not magically rewrite the evidence chain.
    """

    __tablename__ = "sites"

    site_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    postcode: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="Europe/London")
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RoofPlaneRecord(Base):
    """Roof plane geometry used by layout, edge-zone and mounting prechecks."""

    __tablename__ = "roof_planes"

    roof_plane_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    roof_type: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    pitch_deg: Mapped[float] = mapped_column(Float, nullable=False)
    azimuth_deg: Mapped[float] = mapped_column(Float, nullable=False)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    polygon_local_m: Mapped[list | None] = mapped_column(JSON, nullable=True)
    area_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_zone_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ObstructionRecord(Base):
    """Manual/site-survey obstruction block for future shade calculations."""

    __tablename__ = "obstructions"

    obstruction_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), nullable=False, index=True)
    roof_plane_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    obstruction_type: Mapped[str] = mapped_column(String, nullable=False, default="manual_block")
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    polygon_local_m: Mapped[list | None] = mapped_column(JSON, nullable=True)
    centre_local_m: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False, default="manual")
    source_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
