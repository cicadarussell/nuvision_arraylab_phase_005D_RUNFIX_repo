from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.session import get_db, init_db
from app.schemas.catalogue import ProductCreate, ProductQualityLevel, ProductRead
from app.schemas.calculation import CalculationRunCreate, CalculationRunRead
from app.schemas.persistence import ApprovalRequest, ProductDataSnapshotRead, RejectRequest, SpreadsheetImportRead, StagedImportRowRead
from app.schemas.project import ProjectSnapshot
from app.schemas.geometry import (
    CicadaPlannerImportRead, CicadaPlannerImportRequest, MountingPrecheckRead, ObstructionCreate, ObstructionRead, ProjectCreate, ProjectGeometryExportRead, ProjectGeometryRead,
    ProjectRead, ProjectSnapshotRead, RoofPlaneCreate, RoofPlaneRead, SiteCreate, SiteRead,
    GeoJsonRoofImportRequest, GeoJsonRoofImportRead, ProjectGeoJsonExportRead,
    GeometryQualityReportRead, GeometryQualitySnapshotRead, PackerAllowedAreaExportRead, GeometryQualitySelfCheckRead,
    PanelLayoutEditContractRead, PanelPackingCandidateExportRead, PanelPackingGovernanceSelfCheckRead, PanelPackingOverrideCreate, PanelPackingOverrideHistoryRead, PanelPackingOverrideRead, PanelPackingRequest, PanelPackingResultRead, PanelPackingSelfCheckRead, ReviewedPanelModelsRead, SelectedPanelLayoutExportRead,
)
from app.schemas.validation import ValidationReport
from app.schemas.yield_preview import YieldAssumptionSetRead, YieldPreviewRequest, YieldPreviewResultRead, YieldPreviewSelfCheckRead, YieldRunRead
from app.schemas.solar_geometry import SolarGeometryDebugRead, SolarGeometryDebugRequest, SolarGeometrySelfCheckRead
from app.schemas.shade import ShadePreviewRequest, ShadePreviewResultRead, ShadePreviewSelfCheckRead
from app.schemas.datasheet import (
    DatasheetArchiveRead, DatasheetBatchReviewRequest, DatasheetCandidateRead,
    DatasheetDownloadJobRead, DatasheetDownloadQueueRequest, DatasheetDownloadRunRequest, DatasheetOcrJobRead, DatasheetOcrStatusRead,
    DatasheetReviewRead, DatasheetReviewRequest, DatasheetSourceDomainCreate,
    DatasheetSourceDomainRead, DatasheetTablePreviewRead, ProductDesignReadinessRead,
)
from app.schemas.product_application import CurrentProductRead, ProductApplyPreviewRequest, ProductApplyRequest, ProductSnapshotApplicationRead
from app.schemas.commercial import (
    PriceSnapshotRead, PriceStockApplicationRead, PriceStockApplyRequest, PriceStockPreviewRequest,
    QuoteSnapshotCreate, QuoteSnapshotRead, RollbackRecordCreate, RollbackRecordRead, StockSnapshotRead,
)
from app.services.calculation_run import build_calculation_run
from app.services.hash_utils import stable_json_hash
from app.services.self_check import run_backend_self_checks
from app.services.spreadsheet_import_v2 import inspect_workbook_bytes
from app.services.spreadsheet_staging import (
    ImportWorkflowError,
    approve_staged_import,
    get_spreadsheet_import,
    latest_product_data_snapshot,
    list_spreadsheet_imports,
    list_staged_rows,
    reject_staged_import,
    stage_spreadsheet_import,
)
from app.services.validation_engine import validate_product_for_design, validate_project_for_mounting_precheck
from app.services.product_snapshot_apply import (
    ProductApplyWorkflowError,
    apply_product_snapshot_application,
    build_product_apply_preview,
    get_product_snapshot_application,
    get_product_versions,
    list_current_products,
    list_product_snapshot_applications,
    search_current_products,
    validate_current_product_record,
)
from app.services.datasheet_review import (
    DatasheetWorkflowError,
    archive_datasheet_bytes,
    batch_review_candidates,
    create_or_update_source_domain,
    get_datasheet,
    get_datasheet_ocr_status,
    get_datasheet_table_preview,
    list_candidates,
    list_datasheet_review_queue,
    list_datasheet_review_queue_v2,
    list_datasheets,
    list_download_jobs,
    list_reviewed_specs,
    list_source_domains,
    queue_datasheet_download,
    recalculate_product_design_readiness,
    review_candidate,
    run_datasheet_self_check,
    validate_datasheet_source_url,
)
from app.services.commercial_snapshots import (
    CommercialWorkflowError,
    apply_price_stock_application,
    build_price_stock_apply_preview,
    create_quote_snapshot,
    create_rollback_record,
    get_price_stock_application,
    get_quote_snapshot,
    list_latest_price_snapshots,
    list_latest_stock_snapshots,
    list_price_stock_applications,
    list_quote_snapshots,
    list_rollback_records,
)

from app.services.datasheet_downloader import (
    DatasheetDownloadWorkerError,
    datasheet_worker_debug,
    list_ocr_jobs,
    run_datasheet_download_job,
)

from app.services.geometry_import import (
    export_project_geometry,
    geometry_import_self_check,
    import_cicada_planner_geometry,
)
from app.services.map_geometry_sync import (
    export_project_geojson,
    import_geojson_roof,
    map_sync_self_check,
)

from app.services.geometry_quality import (
    build_geometry_quality_report,
    create_geometry_quality_snapshot,
    export_packer_allowed_area,
    geometry_quality_self_check,
)
from app.services.panel_packing import (
    PanelPackingError,
    build_selected_layout_export_payload,
    create_panel_packing_override,
    export_panel_packing_candidate_run,
    list_panel_packing_overrides,
    list_reviewed_panel_model_options,
    panel_layout_edit_contract,
    panel_packing_governance_self_check,
    panel_packing_self_check,
    run_panel_packing,
)

from app.services.yield_preview import (
    YieldPreviewError,
    get_yield_run,
    list_yield_assumption_sets,
    run_yield_preview,
    yield_preview_self_check,
)
from app.services.pvgis_adapter import pvgis_cache_summary
from app.services.solar_geometry import SolarGeometryError, build_solar_geometry_debug, solar_geometry_self_check
from app.services.shade_preview import ShadePreviewError, run_shade_preview, shade_preview_self_check

from app.services.project_geometry import (
    ProjectGeometryError,
    add_obstruction,
    add_roof_plane,
    create_project,
    create_project_snapshot,
    get_project,
    get_project_geometry,
    list_project_snapshots,
    list_projects,
    project_geometry_self_check,
    run_project_mounting_precheck,
    upsert_site,
    validate_project_geometry,
)

router = APIRouter()
_PRODUCTS: list[ProductRead] = []
_CALC_RUNS: list[CalculationRunRead] = []


def _import_read(record) -> SpreadsheetImportRead:
    return SpreadsheetImportRead.model_validate({
        "import_id": record.import_id,
        "file_name": record.file_name,
        "file_hash_sha256": record.file_hash_sha256,
        "status": record.status,
        "uploaded_by": record.uploaded_by,
        "approved_by": record.approved_by,
        "rejected_by": record.rejected_by,
        "validation_report": record.validation_report or {},
        "diff_summary": record.diff_summary or {},
        "staged_row_count": len(record.staged_rows or []),
    })


@router.get("/debug/self-check")
def debug_self_check() -> dict:
    return run_backend_self_checks()


@router.get("/debug/version")
def debug_version() -> dict:
    return {"service": "nuvision-arraylab", "phase": settings.phase, "software_version": settings.software_version, "truth_boundary": settings.truth_boundary}


@router.get("/debug/datasheet-self-check")
def debug_datasheet_self_check(db: Session = Depends(get_db)) -> dict:
    try:
        return run_datasheet_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "failed", "error": str(exc)}


@router.get("/debug/db-self-check")
def debug_db_self_check(db: Session = Depends(get_db)) -> dict:
    try:
        init_db()
        count = len(list_spreadsheet_imports(db))
        latest = latest_product_data_snapshot(db)
        return {
            "status": "ok",
            "database_url_kind": "sqlite" if settings.database_url.startswith("sqlite") else "external",
            "spreadsheet_import_count": count,
            "latest_snapshot_id": latest.snapshot_id if latest else None,
        }
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "failed", "error": str(exc)}



@router.get("/debug/route-map")
def debug_route_map() -> dict:
    routes = []
    for route in router.routes:
        methods = sorted(getattr(route, "methods", []) or [])
        if methods:
            routes.append({"path": f"/api{route.path}", "methods": methods, "name": getattr(route, "name", None)})
    return {
        "status": "ok",
        "phase": settings.phase,
        "route_count": len(routes),
        "routes": sorted(routes, key=lambda item: item["path"]),
    }


@router.get("/debug/restore-check")
def debug_restore_check(db: Session = Depends(get_db)) -> dict:
    checks = {
        "settings_phase": settings.phase,
        "has_spreadsheet_import_tables": True,
        "has_product_snapshot_apply": True,
        "has_commercial_snapshots": True,
        "has_datasheet_review_queue": True,
        "has_datasheet_downloader_worker": True,
        "has_project_geometry_spine": True,
        "has_site_roof_obstruction_records": True,
        "has_cicada_planner_import": True,
        "has_roof_drawing_test_harness": True,
        "has_geometry_export": True,
        "has_maplibre_draft_ui": True,
        "has_geojson_roof_sync": True,
        "has_geometry_quality_reports": True,
        "has_setback_margin_rules": True,
        "has_packer_allowed_area_export": True,
        "has_panel_packing_engine": True,
        "has_panel_packing_evidence_run": True,
        "has_roof_aligned_panel_packing": True,
        "has_candidate_layout_ranking": True,
        "has_panel_geojson_overlay_export": True,
        "has_reviewed_panel_picker_endpoint": True,
        "has_candidate_compare_ui": True,
        "has_candidate_comparison_hash": True,
        "has_mixed_layout_candidates": True,
        "has_aesthetic_row_scoring": True,
        "has_manual_candidate_override_evidence": True,
        "has_panel_packing_candidate_export": True,
        "has_persistent_candidate_override_records": True,
        "has_selected_layout_export_for_yield_stringing_bom": True,
        "has_panel_layout_edit_contract": True,
        "has_candidate_override_history_debug": True,
        "has_preview_yield_engine": True,
        "has_pvgis_backend_adapter": True,
        "has_pvgis_request_cache": True,
        "has_pvgis_t0_comparison": True,
        "has_yield_assumption_sets": True,
        "has_pvgis_backend_request_stub": True,
        "has_monthly_yield_output": True,
        "has_yield_calculation_evidence_run": True,
        "has_solar_position_debug": True,
        "has_pvlib_ready_solar_geometry_service": True,
        "has_roof_plane_incidence_factor_checks": True,
        "has_shade_engine_input_contract": True,
        "has_shade_ray_contract": True,
        "has_obstruction_shadow_preview": True,
        "has_per_panel_shade_debug": True,
        "has_final_mode_reviewed_model_blocker": True,
        "has_test_harness_files": True,
        "structural_truth_boundary": settings.truth_boundary,
    }
    return {
        "status": "ok",
        "restored_from": "NVA_005D_RUNFIX",
        "current_phase": settings.phase,
        "checks": checks,
        "next_human_test": "Run run_dev.bat, draw roof geometry, run packing, export selected layout, run preview yield, run solar geometry debug, then run shade preview.",
    }

@router.get("/products", response_model=list[ProductRead])
def list_products() -> list[ProductRead]:
    return _PRODUCTS


@router.post("/products", response_model=ProductRead)
def create_product(payload: ProductCreate) -> ProductRead:
    product = ProductRead(product_id=f"prd_{len(_PRODUCTS)+1:06d}", quality_level=ProductQualityLevel.Q0, **payload.model_dump())
    _PRODUCTS.append(product)
    return product


@router.post("/products/{product_id}/validate-for-design", response_model=ValidationReport)
def validate_product(product_id: str) -> ValidationReport:
    product = next((p for p in _PRODUCTS if p.product_id == product_id), None)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return validate_product_for_design(product)


@router.post("/projects/validate-mounting-precheck", response_model=ValidationReport)
def validate_project_mounting(payload: ProjectSnapshot) -> ValidationReport:
    return validate_project_for_mounting_precheck(payload)


@router.post("/calculation-runs", response_model=CalculationRunRead)
def create_calculation_run(payload: CalculationRunCreate) -> CalculationRunRead:
    run = build_calculation_run(payload)
    _CALC_RUNS.append(run)
    return run


@router.get("/calculation-runs", response_model=list[CalculationRunRead])
def list_calculation_runs() -> list[CalculationRunRead]:
    return _CALC_RUNS


@router.post("/imports/inspect-spreadsheet")
async def inspect_spreadsheet(file: UploadFile = File(...)) -> dict:
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted for controlled imports.")
    data = await file.read()
    report = inspect_workbook_bytes(file.filename, data)
    return report.model_dump(mode="json")


@router.post("/imports/stage-spreadsheet", response_model=SpreadsheetImportRead)
async def stage_spreadsheet(file: UploadFile = File(...), db: Session = Depends(get_db)) -> SpreadsheetImportRead:
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx files are accepted for controlled imports.")
    data = await file.read()
    record = stage_spreadsheet_import(db, file.filename, data)
    return _import_read(record)


@router.get("/imports", response_model=list[SpreadsheetImportRead])
def list_imports(db: Session = Depends(get_db)) -> list[SpreadsheetImportRead]:
    return [_import_read(record) for record in list_spreadsheet_imports(db)]


@router.get("/imports/{import_id}", response_model=SpreadsheetImportRead)
def get_import(import_id: str, db: Session = Depends(get_db)) -> SpreadsheetImportRead:
    record = get_spreadsheet_import(db, import_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return _import_read(record)


@router.get("/imports/{import_id}/staged-rows", response_model=list[StagedImportRowRead])
def get_staged_rows(import_id: str, db: Session = Depends(get_db)) -> list[StagedImportRowRead]:
    return [StagedImportRowRead.model_validate(row) for row in list_staged_rows(db, import_id)]


@router.post("/imports/{import_id}/approve", response_model=ProductDataSnapshotRead)
def approve_import(import_id: str, payload: ApprovalRequest, db: Session = Depends(get_db)) -> ProductDataSnapshotRead:
    try:
        snapshot = approve_staged_import(db, import_id, payload.approved_by)
        return ProductDataSnapshotRead.model_validate(snapshot)
    except ImportWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/imports/{import_id}/reject", response_model=SpreadsheetImportRead)
def reject_import(import_id: str, payload: RejectRequest, db: Session = Depends(get_db)) -> SpreadsheetImportRead:
    try:
        record = reject_staged_import(db, import_id, payload.rejected_by, payload.reason)
        return _import_read(record)
    except ImportWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/product-data-snapshots/latest", response_model=ProductDataSnapshotRead | None)
def get_latest_snapshot(db: Session = Depends(get_db)) -> ProductDataSnapshotRead | None:
    snapshot = latest_product_data_snapshot(db)
    return ProductDataSnapshotRead.model_validate(snapshot) if snapshot else None


@router.post("/product-data-snapshots/{snapshot_id}/preview-apply", response_model=ProductSnapshotApplicationRead)
def preview_apply_snapshot(snapshot_id: str, payload: ProductApplyPreviewRequest, db: Session = Depends(get_db)) -> ProductSnapshotApplicationRead:
    try:
        application = build_product_apply_preview(db, snapshot_id, created_by=payload.created_by)
        return ProductSnapshotApplicationRead.model_validate(application)
    except ProductApplyWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/snapshot-applications", response_model=list[ProductSnapshotApplicationRead])
def list_snapshot_applications(db: Session = Depends(get_db)) -> list[ProductSnapshotApplicationRead]:
    return [ProductSnapshotApplicationRead.model_validate(app) for app in list_product_snapshot_applications(db)]


@router.get("/snapshot-applications/{application_id}", response_model=ProductSnapshotApplicationRead)
def get_snapshot_application(application_id: str, db: Session = Depends(get_db)) -> ProductSnapshotApplicationRead:
    app = get_product_snapshot_application(db, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Snapshot application not found")
    return ProductSnapshotApplicationRead.model_validate(app)


@router.post("/snapshot-applications/{application_id}/apply", response_model=ProductSnapshotApplicationRead)
def apply_snapshot_application(application_id: str, payload: ProductApplyRequest, db: Session = Depends(get_db)) -> ProductSnapshotApplicationRead:
    try:
        application = apply_product_snapshot_application(db, application_id, applied_by=payload.applied_by)
        return ProductSnapshotApplicationRead.model_validate(application)
    except ProductApplyWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/catalogue/current-products", response_model=list[CurrentProductRead])
def get_current_products(db: Session = Depends(get_db)) -> list[CurrentProductRead]:
    return [CurrentProductRead.model_validate(product) for product in list_current_products(db)]


@router.get("/catalogue/current-products/{product_id}/versions")
def get_current_product_versions(product_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "version_id": version.version_id,
            "product_id": version.product_id,
            "application_id": version.application_id,
            "snapshot_id": version.snapshot_id,
            "product_payload_hash_sha256": version.product_payload_hash_sha256,
            "product_payload": version.product_payload,
        }
        for version in get_product_versions(db, product_id)
    ]


@router.post("/product-data-snapshots/{snapshot_id}/preview-price-stock", response_model=PriceStockApplicationRead)
def preview_price_stock(snapshot_id: str, payload: PriceStockPreviewRequest, db: Session = Depends(get_db)) -> PriceStockApplicationRead:
    try:
        app = build_price_stock_apply_preview(db, snapshot_id, created_by=payload.created_by)
        return PriceStockApplicationRead.model_validate(app)
    except CommercialWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/price-stock-applications", response_model=list[PriceStockApplicationRead])
def get_price_stock_applications(db: Session = Depends(get_db)) -> list[PriceStockApplicationRead]:
    return [PriceStockApplicationRead.model_validate(app) for app in list_price_stock_applications(db)]


@router.get("/price-stock-applications/{application_id}", response_model=PriceStockApplicationRead)
def get_price_stock_application_route(application_id: str, db: Session = Depends(get_db)) -> PriceStockApplicationRead:
    app = get_price_stock_application(db, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Price/stock application not found")
    return PriceStockApplicationRead.model_validate(app)


@router.post("/price-stock-applications/{application_id}/apply", response_model=PriceStockApplicationRead)
def apply_price_stock(application_id: str, payload: PriceStockApplyRequest, db: Session = Depends(get_db)) -> PriceStockApplicationRead:
    try:
        app = apply_price_stock_application(db, application_id, applied_by=payload.applied_by)
        return PriceStockApplicationRead.model_validate(app)
    except CommercialWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/catalogue/price-snapshots/latest", response_model=list[PriceSnapshotRead])
def get_latest_price_snapshots(db: Session = Depends(get_db)) -> list[PriceSnapshotRead]:
    return [PriceSnapshotRead.model_validate(row) for row in list_latest_price_snapshots(db)]


@router.get("/catalogue/stock-snapshots/latest", response_model=list[StockSnapshotRead])
def get_latest_stock_snapshots(db: Session = Depends(get_db)) -> list[StockSnapshotRead]:
    return [StockSnapshotRead.model_validate(row) for row in list_latest_stock_snapshots(db)]


@router.get("/catalogue/search", response_model=list[CurrentProductRead])
def search_catalogue(
    q: str | None = Query(default=None),
    manufacturer: str | None = Query(default=None),
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    quality_level: str | None = Query(default=None),
    design_ready: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[CurrentProductRead]:
    return [CurrentProductRead.model_validate(product) for product in search_current_products(db, q, manufacturer, category, status, quality_level, design_ready)]


@router.post("/catalogue/current-products/{product_id}/validate-persistent")
def validate_persistent_current_product(product_id: str, db: Session = Depends(get_db)) -> dict:
    report = validate_current_product_record(db, product_id)
    if report["status"] == "blocked" and any(i.get("code") == "PRODUCT_NOT_FOUND" for i in report.get("issues", [])):
        raise HTTPException(status_code=404, detail="Current product not found")
    return report


@router.post("/quotes/snapshot", response_model=QuoteSnapshotRead)
def create_quote_snapshot_route(payload: QuoteSnapshotCreate, db: Session = Depends(get_db)) -> QuoteSnapshotRead:
    quote = create_quote_snapshot(db, payload.project_id, payload.product_ids, created_by=payload.created_by, note=payload.note)
    return QuoteSnapshotRead.model_validate(quote)


@router.get("/quotes", response_model=list[QuoteSnapshotRead])
def list_quotes(db: Session = Depends(get_db)) -> list[QuoteSnapshotRead]:
    return [QuoteSnapshotRead.model_validate(quote) for quote in list_quote_snapshots(db)]


@router.get("/quotes/{quote_id}", response_model=QuoteSnapshotRead)
def get_quote(quote_id: str, db: Session = Depends(get_db)) -> QuoteSnapshotRead:
    quote = get_quote_snapshot(db, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="Quote snapshot not found")
    return QuoteSnapshotRead.model_validate(quote)


@router.post("/rollback-records", response_model=RollbackRecordRead)
def create_rollback(payload: RollbackRecordCreate, db: Session = Depends(get_db)) -> RollbackRecordRead:
    record = create_rollback_record(db, payload.target_type, payload.target_id, payload.reason, requested_by=payload.requested_by, payload=payload.payload)
    return RollbackRecordRead.model_validate(record)


@router.get("/rollback-records", response_model=list[RollbackRecordRead])
def get_rollback_records(db: Session = Depends(get_db)) -> list[RollbackRecordRead]:
    return [RollbackRecordRead.model_validate(record) for record in list_rollback_records(db)]




@router.post("/datasheet-source-domains", response_model=DatasheetSourceDomainRead)
def upsert_datasheet_source_domain(payload: DatasheetSourceDomainCreate, db: Session = Depends(get_db)) -> DatasheetSourceDomainRead:
    try:
        row = create_or_update_source_domain(
            db,
            payload.domain,
            status=payload.status,
            source_kind=payload.source_kind,
            notes=payload.notes,
            created_by=payload.created_by,
        )
        return DatasheetSourceDomainRead.model_validate(row)
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/datasheet-source-domains", response_model=list[DatasheetSourceDomainRead])
def get_datasheet_source_domains(db: Session = Depends(get_db)) -> list[DatasheetSourceDomainRead]:
    return [DatasheetSourceDomainRead.model_validate(row) for row in list_source_domains(db)]


@router.get("/datasheet-source-domains/validate-url")
def validate_datasheet_url(source_url: str = Query(...), db: Session = Depends(get_db)) -> dict:
    return validate_datasheet_source_url(db, source_url)


@router.post("/datasheet-download-jobs", response_model=DatasheetDownloadJobRead)
def create_datasheet_download_job(payload: DatasheetDownloadQueueRequest, db: Session = Depends(get_db)) -> DatasheetDownloadJobRead:
    try:
        row = queue_datasheet_download(db, source_url=payload.source_url, product_id=payload.product_id, requested_by=payload.requested_by)
        return DatasheetDownloadJobRead.model_validate(row)
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/datasheet-download-jobs", response_model=list[DatasheetDownloadJobRead])
def get_datasheet_download_jobs(db: Session = Depends(get_db)) -> list[DatasheetDownloadJobRead]:
    return [DatasheetDownloadJobRead.model_validate(row) for row in list_download_jobs(db)]




@router.post("/datasheet-download-jobs/{job_id}/run", response_model=DatasheetDownloadJobRead)
def run_datasheet_download_job_route(job_id: str, payload: DatasheetDownloadRunRequest, db: Session = Depends(get_db)) -> DatasheetDownloadJobRead:
    try:
        job = run_datasheet_download_job(db, job_id, run_by=payload.run_by)
        return DatasheetDownloadJobRead.model_validate(job)
    except DatasheetDownloadWorkerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/datasheet-download-jobs/debug")
def datasheet_download_jobs_debug(db: Session = Depends(get_db)) -> dict:
    return datasheet_worker_debug(db)


@router.get("/datasheet-ocr-jobs", response_model=list[DatasheetOcrJobRead])
def get_datasheet_ocr_jobs(db: Session = Depends(get_db)) -> list[DatasheetOcrJobRead]:
    return [DatasheetOcrJobRead.model_validate(row) for row in list_ocr_jobs(db)]

@router.get("/datasheet-review-queue")
def get_datasheet_review_queue(db: Session = Depends(get_db)) -> list[dict]:
    return list_datasheet_review_queue(db)


@router.get("/datasheet-review-queue/v2")
def get_datasheet_review_queue_v2(db: Session = Depends(get_db)) -> list[dict]:
    return list_datasheet_review_queue_v2(db)


@router.post("/datasheet-candidates/batch-review", response_model=list[DatasheetReviewRead])
def batch_review_datasheet_candidates(payload: DatasheetBatchReviewRequest, db: Session = Depends(get_db)) -> list[DatasheetReviewRead]:
    try:
        records = batch_review_candidates(db, payload.candidate_ids, action=payload.action, reviewer=payload.reviewer, reason=payload.reason)
        return [DatasheetReviewRead.model_validate(record) for record in records]
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/datasheets/archive", response_model=DatasheetArchiveRead)
async def archive_datasheet(
    file: UploadFile = File(...),
    product_id: str | None = Query(default=None),
    source_url: str | None = Query(default=None),
    uploaded_by: str | None = Query(default="unassigned_reviewer"),
    db: Session = Depends(get_db),
) -> DatasheetArchiveRead:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf datasheets are accepted.")
    data = await file.read()
    try:
        record = archive_datasheet_bytes(db, file_name=file.filename, data=data, product_id=product_id, source_url=source_url, uploaded_by=uploaded_by)
        return DatasheetArchiveRead.model_validate(record)
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/datasheets", response_model=list[DatasheetArchiveRead])
def get_datasheets(db: Session = Depends(get_db)) -> list[DatasheetArchiveRead]:
    return [DatasheetArchiveRead.model_validate(row) for row in list_datasheets(db)]


@router.get("/datasheets/{datasheet_id}", response_model=DatasheetArchiveRead)
def get_datasheet_route(datasheet_id: str, db: Session = Depends(get_db)) -> DatasheetArchiveRead:
    record = get_datasheet(db, datasheet_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Datasheet not found")
    return DatasheetArchiveRead.model_validate(record)


@router.get("/datasheets/{datasheet_id}/candidates", response_model=list[DatasheetCandidateRead])
def get_datasheet_candidates(datasheet_id: str, db: Session = Depends(get_db)) -> list[DatasheetCandidateRead]:
    return [DatasheetCandidateRead.model_validate(row) for row in list_candidates(db, datasheet_id=datasheet_id)]


@router.get("/datasheets/{datasheet_id}/table-preview", response_model=DatasheetTablePreviewRead)
def get_datasheet_table_preview_route(datasheet_id: str, db: Session = Depends(get_db)) -> DatasheetTablePreviewRead:
    try:
        return DatasheetTablePreviewRead.model_validate(get_datasheet_table_preview(db, datasheet_id))
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasheets/{datasheet_id}/ocr-status", response_model=DatasheetOcrStatusRead)
def get_datasheet_ocr_status_route(datasheet_id: str, db: Session = Depends(get_db)) -> DatasheetOcrStatusRead:
    try:
        return DatasheetOcrStatusRead.model_validate(get_datasheet_ocr_status(db, datasheet_id))
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/catalogue/current-products/{product_id}/datasheet-candidates", response_model=list[DatasheetCandidateRead])
def get_product_datasheet_candidates(product_id: str, db: Session = Depends(get_db)) -> list[DatasheetCandidateRead]:
    return [DatasheetCandidateRead.model_validate(row) for row in list_candidates(db, product_id=product_id)]


@router.get("/catalogue/current-products/{product_id}/reviewed-specs")
def get_product_reviewed_specs(product_id: str, db: Session = Depends(get_db)) -> list[dict]:
    return [
        {
            "spec_id": spec.spec_id,
            "product_id": spec.product_id,
            "field_name": spec.field_name,
            "value_text": spec.value_text,
            "value_number": spec.value_number,
            "unit": spec.unit,
            "quality_level": spec.quality_level,
            "source_type": spec.source_type,
            "source_file_hash_sha256": spec.source_file_hash_sha256,
            "review_status": spec.review_status,
            "reviewed_by": spec.reviewed_by,
        }
        for spec in list_reviewed_specs(db, product_id)
    ]


@router.post("/catalogue/current-products/{product_id}/recalculate-design-readiness", response_model=ProductDesignReadinessRead)
def recalculate_current_product_design_readiness(product_id: str, db: Session = Depends(get_db)) -> ProductDesignReadinessRead:
    try:
        return ProductDesignReadinessRead.model_validate(recalculate_product_design_readiness(db, product_id))
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/datasheet-candidates/{candidate_id}/review", response_model=DatasheetReviewRead)
def review_datasheet_candidate(candidate_id: str, payload: DatasheetReviewRequest, db: Session = Depends(get_db)) -> DatasheetReviewRead:
    try:
        record = review_candidate(
            db,
            candidate_id,
            action=payload.action,
            reviewer=payload.reviewer,
            corrected_value_text=payload.corrected_value_text,
            corrected_value_number=payload.corrected_value_number,
            corrected_unit=payload.corrected_unit,
            reason=payload.reason,
            selected_manufacturer_model=payload.selected_manufacturer_model,
            selected_datasheet_variant=payload.selected_datasheet_variant,
            model_selection_basis=payload.model_selection_basis,
        )
        return DatasheetReviewRead.model_validate(record)
    except DatasheetWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc




@router.get("/debug/geometry-self-check")
def debug_geometry_self_check(db: Session = Depends(get_db)) -> dict:
    try:
        return project_geometry_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "failed", "error": str(exc)}


@router.get("/debug/geometry-import-self-check")
def debug_geometry_import_self_check(db: Session = Depends(get_db)) -> dict:
    try:
        return geometry_import_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "failed", "error": str(exc)}


@router.get("/debug/map-sync-self-check")
def debug_map_sync_self_check(db: Session = Depends(get_db)) -> dict:
    try:
        return map_sync_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return {"status": "failed", "error": str(exc)}


@router.post("/projects", response_model=ProjectRead)
def create_project_route(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    try:
        return ProjectRead.model_validate(create_project(db, payload), from_attributes=True)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects", response_model=list[ProjectRead])
def list_projects_route(db: Session = Depends(get_db)) -> list[ProjectRead]:
    return [ProjectRead.model_validate(row, from_attributes=True) for row in list_projects(db)]


@router.get("/projects/{project_id}/geometry", response_model=ProjectGeometryRead)
def get_project_geometry_route(project_id: str, db: Session = Depends(get_db)) -> ProjectGeometryRead:
    try:
        return get_project_geometry(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/site", response_model=SiteRead)
def upsert_project_site_route(project_id: str, payload: SiteCreate, db: Session = Depends(get_db)) -> SiteRead:
    try:
        return SiteRead.model_validate(upsert_site(db, project_id, payload), from_attributes=True)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/roof-planes", response_model=RoofPlaneRead)
def add_project_roof_plane_route(project_id: str, payload: RoofPlaneCreate, db: Session = Depends(get_db)) -> RoofPlaneRead:
    try:
        row = add_roof_plane(db, project_id, payload)
        return RoofPlaneRead.model_validate(row, from_attributes=True)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/obstructions", response_model=ObstructionRead)
def add_project_obstruction_route(project_id: str, payload: ObstructionCreate, db: Session = Depends(get_db)) -> ObstructionRead:
    try:
        row = add_obstruction(db, project_id, payload)
        return ObstructionRead.model_validate(row, from_attributes=True)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/snapshots", response_model=ProjectSnapshotRead)
def create_project_snapshot_route(project_id: str, created_by: str | None = Query(default=None), db: Session = Depends(get_db)) -> ProjectSnapshotRead:
    try:
        row = create_project_snapshot(db, project_id, created_by=created_by)
        return ProjectSnapshotRead.model_validate(row, from_attributes=True)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/snapshots", response_model=list[ProjectSnapshotRead])
def list_project_snapshots_route(project_id: str, db: Session = Depends(get_db)) -> list[ProjectSnapshotRead]:
    if get_project(db, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return [ProjectSnapshotRead.model_validate(row, from_attributes=True) for row in list_project_snapshots(db, project_id)]


@router.get("/projects/{project_id}/validate-geometry", response_model=ValidationReport)
def validate_project_geometry_route(project_id: str, db: Session = Depends(get_db)) -> ValidationReport:
    try:
        return validate_project_geometry(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/mounting-precheck", response_model=MountingPrecheckRead)
def run_project_mounting_precheck_route(project_id: str, created_by: str | None = Query(default=None), db: Session = Depends(get_db)) -> MountingPrecheckRead:
    try:
        return run_project_mounting_precheck(db, project_id, created_by=created_by)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/geometry/import-cicada-planner", response_model=CicadaPlannerImportRead)
def import_cicada_planner_geometry_route(project_id: str, payload: CicadaPlannerImportRequest, db: Session = Depends(get_db)) -> CicadaPlannerImportRead:
    try:
        return import_cicada_planner_geometry(db, project_id, payload)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/geometry/export", response_model=ProjectGeometryExportRead)
def export_project_geometry_route(project_id: str, db: Session = Depends(get_db)) -> ProjectGeometryExportRead:
    try:
        return export_project_geometry(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/geometry/import-geojson-roof", response_model=GeoJsonRoofImportRead)
def import_geojson_roof_route(project_id: str, payload: GeoJsonRoofImportRequest, db: Session = Depends(get_db)) -> GeoJsonRoofImportRead:
    try:
        return import_geojson_roof(db, project_id, payload)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/geometry/export-geojson", response_model=ProjectGeoJsonExportRead)
def export_project_geojson_route(project_id: str, db: Session = Depends(get_db)) -> ProjectGeoJsonExportRead:
    try:
        return export_project_geojson(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/debug/geometry-quality-self-check", response_model=GeometryQualitySelfCheckRead)
def debug_geometry_quality_self_check(db: Session = Depends(get_db)) -> GeometryQualitySelfCheckRead:
    try:
        return geometry_quality_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return GeometryQualitySelfCheckRead(
            status="failed",
            bad_polygon_blocked=False,
            obstruction_reduces_usable_area=False,
            usable_area_positive=False,
            quality_hash_stable=False,
            report_hash_sha256=str(exc),
        )


@router.get("/projects/{project_id}/geometry/quality", response_model=GeometryQualityReportRead)
def project_geometry_quality_route(project_id: str, db: Session = Depends(get_db)) -> GeometryQualityReportRead:
    try:
        return build_geometry_quality_report(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{project_id}/geometry/quality-snapshot", response_model=GeometryQualitySnapshotRead)
def project_geometry_quality_snapshot_route(project_id: str, created_by: str | None = Query(default=None), db: Session = Depends(get_db)) -> GeometryQualitySnapshotRead:
    try:
        return create_geometry_quality_snapshot(db, project_id, created_by=created_by)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/geometry/packer-allowed-area", response_model=PackerAllowedAreaExportRead)
def project_packer_allowed_area_route(project_id: str, db: Session = Depends(get_db)) -> PackerAllowedAreaExportRead:
    try:
        return export_packer_allowed_area(db, project_id)
    except ProjectGeometryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/catalogue/reviewed-panel-models", response_model=ReviewedPanelModelsRead)
def catalogue_reviewed_panel_models_route(include_dev_fallback: bool = Query(default=False), db: Session = Depends(get_db)) -> ReviewedPanelModelsRead:
    return list_reviewed_panel_model_options(db, include_dev_fallback=include_dev_fallback)


@router.get("/debug/panel-packing-self-check", response_model=PanelPackingSelfCheckRead)
def debug_panel_packing_self_check(db: Session = Depends(get_db)) -> PanelPackingSelfCheckRead:
    try:
        return panel_packing_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return PanelPackingSelfCheckRead(
            status="failed",
            project_id="error",
            preview_panels_fit=False,
            final_blocks_dev_fallback=False,
            invalid_geometry_blocked=False,
            roof_aligned_candidate_differs=False,
            score_mode_changes_selection=False,
            calculation_run_id=None,
            output_hash_sha256=str(exc),
        )


@router.post("/projects/{project_id}/panel-packing/run", response_model=PanelPackingResultRead)
def project_panel_packing_route(project_id: str, payload: PanelPackingRequest, db: Session = Depends(get_db)) -> PanelPackingResultRead:
    try:
        return run_panel_packing(db, project_id, payload)
    except (ProjectGeometryError, PanelPackingError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/panel-packing/runs/{calculation_run_id}/candidate-export", response_model=PanelPackingCandidateExportRead)
def project_panel_packing_candidate_export_route(project_id: str, calculation_run_id: str, db: Session = Depends(get_db)) -> PanelPackingCandidateExportRead:
    try:
        return export_panel_packing_candidate_run(db, project_id, calculation_run_id)
    except PanelPackingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/debug/panel-packing-governance-self-check", response_model=PanelPackingGovernanceSelfCheckRead)
def debug_panel_packing_governance_self_check(db: Session = Depends(get_db)) -> PanelPackingGovernanceSelfCheckRead:
    try:
        return panel_packing_governance_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return PanelPackingGovernanceSelfCheckRead(
            status="failed",
            project_id="error",
            override_history_immutable=False,
            final_override_blocked_from_preview_fallback=False,
            selected_layout_export_hash_stable=False,
            layout_edit_contract_available=False,
            override_count=0,
            selected_layout_export_hash_sha256=str(exc),
        )


@router.post("/projects/{project_id}/panel-packing/runs/{calculation_run_id}/overrides", response_model=PanelPackingOverrideRead)
def project_panel_packing_override_create_route(project_id: str, calculation_run_id: str, payload: PanelPackingOverrideCreate, db: Session = Depends(get_db)) -> PanelPackingOverrideRead:
    try:
        return create_panel_packing_override(db, project_id, calculation_run_id, payload)
    except PanelPackingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/panel-packing/overrides", response_model=PanelPackingOverrideHistoryRead)
def project_panel_packing_override_history_route(project_id: str, db: Session = Depends(get_db)) -> PanelPackingOverrideHistoryRead:
    return list_panel_packing_overrides(db, project_id)


@router.get("/projects/{project_id}/panel-packing/runs/{calculation_run_id}/selected-layout-export", response_model=SelectedPanelLayoutExportRead)
def project_panel_packing_selected_layout_export_route(project_id: str, calculation_run_id: str, db: Session = Depends(get_db)) -> SelectedPanelLayoutExportRead:
    try:
        return build_selected_layout_export_payload(db, project_id, calculation_run_id)
    except PanelPackingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/panel-packing/layout-edit-contract", response_model=PanelLayoutEditContractRead)
def project_panel_layout_edit_contract_route(project_id: str) -> PanelLayoutEditContractRead:
    return panel_layout_edit_contract(project_id)


@router.get("/debug/yield-preview-self-check", response_model=YieldPreviewSelfCheckRead)
def debug_yield_preview_self_check(db: Session = Depends(get_db)) -> YieldPreviewSelfCheckRead:
    try:
        return yield_preview_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return YieldPreviewSelfCheckRead(
            status="failed",
            project_id="error",
            panel_layout_required_blocked=False,
            annual_kwh_positive=False,
            monthly_sum_matches_annual=False,
            assumption_change_changes_hash=False,
            pvgis_stub_backend_only=False,
            calculation_run_id=None,
            output_hash_sha256=str(exc),
        )


@router.get("/debug/pvgis-cache")
def debug_pvgis_cache_route(db: Session = Depends(get_db)) -> dict:
    return pvgis_cache_summary(db)




@router.get("/debug/solar-geometry-self-check", response_model=SolarGeometrySelfCheckRead)
def debug_solar_geometry_self_check(db: Session = Depends(get_db)) -> SolarGeometrySelfCheckRead:
    try:
        return solar_geometry_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return SolarGeometrySelfCheckRead(
            status="failed",
            pvlib_available=False,
            source_engine="error",
            south_35_beats_north_35=False,
            tilt_change_changes_hash=False,
            azimuth_change_changes_factor=False,
            sample_count_ok=False,
            incidence_math_bounds_ok=False,
            calculation_hash_changes_with_geometry=False,
            project_id="error",
            output_hash_sha256=str(exc),
        )


@router.post("/projects/{project_id}/yield/solar-geometry-debug", response_model=SolarGeometryDebugRead)
def project_solar_geometry_debug_route(project_id: str, payload: SolarGeometryDebugRequest, db: Session = Depends(get_db)) -> SolarGeometryDebugRead:
    try:
        return build_solar_geometry_debug(db, project_id, payload)
    except (SolarGeometryError, ProjectGeometryError, PanelPackingError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/yield/assumption-sets", response_model=list[YieldAssumptionSetRead])
def yield_assumption_sets_route(db: Session = Depends(get_db)) -> list[YieldAssumptionSetRead]:
    return list_yield_assumption_sets(db)


@router.post("/projects/{project_id}/yield/preview", response_model=YieldPreviewResultRead)
def project_yield_preview_route(project_id: str, payload: YieldPreviewRequest, db: Session = Depends(get_db)) -> YieldPreviewResultRead:
    try:
        return run_yield_preview(db, project_id, payload)
    except (YieldPreviewError, ProjectGeometryError, PanelPackingError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/yield/runs/{calculation_run_id}", response_model=YieldRunRead)
def project_yield_run_route(project_id: str, calculation_run_id: str, db: Session = Depends(get_db)) -> YieldRunRead:
    try:
        return get_yield_run(db, project_id, calculation_run_id)
    except YieldPreviewError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/debug/shade-preview-self-check", response_model=ShadePreviewSelfCheckRead)
def debug_shade_preview_self_check(db: Session = Depends(get_db)) -> ShadePreviewSelfCheckRead:
    try:
        init_db()
        return shade_preview_self_check(db)
    except Exception as exc:  # pragma: no cover - defensive diagnostic path
        return ShadePreviewSelfCheckRead(
            status="failed",
            project_id="error",
            shade_changes_with_obstruction_height=False,
            shade_changes_with_obstruction_position=False,
            missing_obstruction_height_blocks=False,
            worst_panel_list_present=False,
            shade_hash_changes_with_geometry=False,
            sample_bounds_ok=False,
            calculation_run_id=None,
            output_hash_sha256=str(exc),
        )


@router.post("/projects/{project_id}/shade/preview", response_model=ShadePreviewResultRead)
def project_shade_preview_route(project_id: str, payload: ShadePreviewRequest, db: Session = Depends(get_db)) -> ShadePreviewResultRead:
    try:
        return run_shade_preview(db, project_id, payload)
    except (ShadePreviewError, ProjectGeometryError, PanelPackingError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/hash")
def hash_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object.")
    return {"sha256": stable_json_hash(payload)}
