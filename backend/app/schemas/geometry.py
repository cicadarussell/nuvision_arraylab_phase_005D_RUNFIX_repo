from __future__ import annotations

from app.core.compat import StrEnum
from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.project import RoofType, StructuralTruthState
from app.schemas.validation import ValidationReport


class ProjectStatus(StrEnum):
    draft = "draft"
    survey_required = "survey_required"
    design_draft = "design_draft"
    quote_ready = "quote_ready"
    archived = "archived"


class SourceConfidenceKind(StrEnum):
    manual = "manual"
    postcode_lookup = "postcode_lookup"
    map_drawn = "map_drawn"
    survey = "survey"
    lidar = "lidar"
    imported = "imported"


class ObstructionType(StrEnum):
    manual_block = "manual_block"
    chimney = "chimney"
    dormer = "dormer"
    skylight = "skylight"
    tree = "tree"
    neighbouring_building = "neighbouring_building"
    horizon = "horizon"


class ProjectCreate(BaseModel):
    project_id: str | None = None
    title: str = Field(min_length=1, max_length=160)
    customer_ref: str | None = None
    created_by: str | None = None


class ProjectRead(ProjectCreate):
    project_id: str
    status: ProjectStatus = ProjectStatus.draft


class SiteCreate(BaseModel):
    postcode: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "Europe/London"
    source_type: SourceConfidenceKind = SourceConfidenceKind.manual
    source_confidence: float = Field(default=0.5, ge=0, le=1)
    notes: str | None = None

    @model_validator(mode="after")
    def require_location_hint(self):
        if not self.postcode and (self.lat is None or self.lon is None):
            raise ValueError("site needs either postcode or lat/lon")
        if (self.lat is None) ^ (self.lon is None):
            raise ValueError("lat and lon must be provided together")
        return self


class SiteRead(SiteCreate):
    site_id: str
    project_id: str


class RoofPlaneCreate(BaseModel):
    roof_plane_id: str | None = None
    label: str | None = None
    roof_type: RoofType = RoofType.unknown
    pitch_deg: float = Field(ge=0, le=75)
    azimuth_deg: float = Field(ge=0, lt=360)
    height_m: float | None = Field(default=None, ge=0)
    polygon_local_m: list[list[float]] | None = None
    source_type: SourceConfidenceKind = SourceConfidenceKind.manual
    source_confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("polygon_local_m")
    @classmethod
    def validate_polygon(cls, value):
        if value is None:
            return value
        if len(value) < 3:
            raise ValueError("roof polygon must contain at least three points")
        for pt in value:
            if len(pt) != 2:
                raise ValueError("each polygon point must be [x_m, y_m]")
        return value


class RoofPlaneRead(RoofPlaneCreate):
    roof_plane_id: str
    project_id: str
    area_m2: float | None = None
    edge_zone_depth_m: float | None = None


class ObstructionCreate(BaseModel):
    obstruction_id: str | None = None
    roof_plane_id: str | None = None
    obstruction_type: ObstructionType = ObstructionType.manual_block
    label: str | None = None
    height_m: float | None = Field(default=None, ge=0)
    polygon_local_m: list[list[float]] | None = None
    centre_local_m: list[float] | None = None
    source_type: SourceConfidenceKind = SourceConfidenceKind.manual
    source_confidence: float = Field(default=0.5, ge=0, le=1)
    notes: str | None = None

    @field_validator("polygon_local_m")
    @classmethod
    def validate_polygon(cls, value):
        if value is None:
            return value
        if len(value) < 3:
            raise ValueError("obstruction polygon must contain at least three points")
        for pt in value:
            if len(pt) != 2:
                raise ValueError("each polygon point must be [x_m, y_m]")
        return value

    @field_validator("centre_local_m")
    @classmethod
    def validate_centre(cls, value):
        if value is not None and len(value) != 2:
            raise ValueError("centre_local_m must be [x_m, y_m]")
        return value


class ObstructionRead(ObstructionCreate):
    obstruction_id: str
    project_id: str


class ProjectGeometryRead(BaseModel):
    project: ProjectRead
    site: SiteRead | None = None
    roof_planes: list[RoofPlaneRead] = Field(default_factory=list)
    obstructions: list[ObstructionRead] = Field(default_factory=list)
    latest_snapshot_id: str | None = None
    latest_snapshot_hash_sha256: str | None = None


class ProjectSnapshotRead(BaseModel):
    project_snapshot_id: str
    project_id: str
    snapshot_kind: str
    snapshot_hash_sha256: str
    snapshot_payload: dict


class MountingPrecheckRead(BaseModel):
    project_id: str
    structural_truth_state: StructuralTruthState
    calculation_run_id: str
    calculation_status: str
    validation_report: ValidationReport
    input_snapshot_hash_sha256: str
    output_hash_sha256: str | None = None


class GeometrySelfCheckRead(BaseModel):
    status: str
    project_id: str
    blocked_missing_height: bool
    ok_known_roof_state: str
    snapshot_hash_stable: bool
    roof_plane_area_m2: float | None


class CicadaPlannerImportRequest(BaseModel):
    """Import payload from the older CICADA Solar Field Planner V2 export JSON.

    The importer treats this as geometry evidence only. It creates roof/array planes for
    test and planning workflows, not final install approval. Humans, naturally, still have
    to measure the actual roof/field rather than worshipping a browser export.
    """

    planner_payload: dict
    created_by: str | None = None
    default_roof_type: RoofType = RoofType.unknown
    default_height_m: float | None = Field(default=None, ge=0)
    source_confidence: float = Field(default=0.45, ge=0, le=1)
    import_arrays_as_roof_planes: bool = True
    import_boundary_as_obstruction: bool = False


class CicadaPlannerImportRead(BaseModel):
    project_id: str
    import_status: str
    planner_app: str | None = None
    arrays_seen: int = 0
    roof_planes_created: int = 0
    obstructions_created: int = 0
    site_created_or_updated: bool = False
    geometry_snapshot_id: str | None = None
    geometry_snapshot_hash_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ProjectGeometryExportRead(BaseModel):
    project_id: str
    export_kind: str = "arraylab_local_geometry_v0"
    units: str = "metres_local_project_frame"
    geometry_snapshot_id: str | None = None
    geometry_snapshot_hash_sha256: str | None = None
    payload: dict


class GeoJsonRoofImportRequest(BaseModel):
    """Import a single roof polygon drawn in a MapLibre/GeoJSON frontend.

    Coordinates must follow RFC 7946 GeoJSON order: [longitude, latitude].
    The backend converts the polygon into the local metre frame used by the
    existing roof geometry spine. This is survey/planning evidence only.
    """

    geojson: dict
    label: str | None = None
    roof_type: RoofType = RoofType.unknown
    pitch_deg: float = Field(default=35, ge=0, le=75)
    azimuth_deg: float = Field(default=180, ge=0, lt=360)
    height_m: float | None = Field(default=None, ge=0)
    source_confidence: float = Field(default=0.55, ge=0, le=1)
    created_by: str | None = None


class GeoJsonRoofImportRead(BaseModel):
    project_id: str
    roof_plane_id: str
    import_status: str
    points_imported: int
    area_m2: float | None = None
    edge_zone_depth_m: float | None = None
    validation_status: str
    geometry_snapshot_id: str | None = None
    geometry_snapshot_hash_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)
    source_crs: str = "RFC7946_WGS84_lon_lat"


class ProjectGeoJsonExportRead(BaseModel):
    project_id: str
    export_kind: str = "arraylab_project_geojson_v0"
    geometry_snapshot_id: str | None = None
    geometry_snapshot_hash_sha256: str | None = None
    feature_collection: dict
    truth_boundary: str


class MapSyncSelfCheckRead(BaseModel):
    status: str
    project_id: str
    roof_plane_id: str | None = None
    imported_area_m2: float | None = None
    exported_feature_count: int = 0
    validation_status: str
    closed_ring_exported: bool


class PolygonDebugPayload(BaseModel):
    polygon_local_m: list[list[float]]


class SetbackRuleRead(BaseModel):
    rule_id: str
    roof_type: RoofType
    edge_margin_m: float = Field(ge=0)
    obstruction_clearance_m: float = Field(ge=0)
    access_margin_m: float = Field(ge=0)
    rule_source: str
    confidence: float = Field(ge=0, le=1)
    final_design_authority: str = "manufacturer_or_engineer"


class RoofPlaneQualityRead(BaseModel):
    roof_plane_id: str
    label: str | None = None
    roof_type: RoofType = RoofType.unknown
    geometry_quality_score: float = Field(ge=0, le=100)
    original_area_m2: float | None = None
    usable_area_m2: float | None = None
    blocked_area_m2: float | None = None
    edge_margin_m: float | None = None
    obstruction_clearance_m: float | None = None
    edge_zone_depth_m: float | None = None
    polygon_valid: bool
    usable_polygon_local_m: list[list[float]] | None = None
    packer_allowed_area: dict | None = None
    issue_codes: list[str] = Field(default_factory=list)


class GeometryQualityReportRead(BaseModel):
    project_id: str
    status: str
    report_hash_sha256: str
    validation_report: ValidationReport
    setback_rules: list[SetbackRuleRead] = Field(default_factory=list)
    roof_planes: list[RoofPlaneQualityRead] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class GeometryQualitySnapshotRead(BaseModel):
    project_id: str
    project_snapshot_id: str
    snapshot_hash_sha256: str
    quality_report_hash_sha256: str
    report: GeometryQualityReportRead


class PackerAllowedAreaExportRead(BaseModel):
    project_id: str
    export_kind: str = "arraylab_packer_allowed_area_v0"
    units: str = "metres_local_project_frame"
    report_hash_sha256: str
    payload: dict


class GeometryQualitySelfCheckRead(BaseModel):
    status: str
    bad_polygon_blocked: bool
    obstruction_reduces_usable_area: bool
    usable_area_positive: bool
    quality_hash_stable: bool
    report_hash_sha256: str | None = None


class PanelPackingDesignMode(StrEnum):
    preview = "preview"
    final = "final"


class PanelPackingOrientation(StrEnum):
    portrait = "portrait"
    landscape = "landscape"


class PanelPackingAlignment(StrEnum):
    axis_aligned = "axis_aligned"
    roof_azimuth = "roof_azimuth"


class PanelPackingScoreGoal(StrEnum):
    max_kwp = "max_kwp"
    best_fit = "best_fit"
    fewer_panels = "fewer_panels"
    aesthetic = "aesthetic"


class PanelCandidateLayoutMode(StrEnum):
    single_orientation = "single_orientation"
    mixed_portrait_landscape = "mixed_portrait_landscape"
    all = "all"


class PanelPackingRequest(BaseModel):
    roof_plane_ids: list[str] | None = None
    panel_product_ids: list[str] | None = None
    allow_dev_fallback_panels: bool = True
    design_mode: PanelPackingDesignMode = PanelPackingDesignMode.preview
    candidate_orientations: list[PanelPackingOrientation] = Field(default_factory=lambda: [PanelPackingOrientation.portrait, PanelPackingOrientation.landscape])
    packing_alignment: PanelPackingAlignment = PanelPackingAlignment.roof_azimuth
    score_goal: PanelPackingScoreGoal = PanelPackingScoreGoal.max_kwp
    compare_candidates: bool = True
    candidate_layout_mode: PanelCandidateLayoutMode = PanelCandidateLayoutMode.all
    selected_candidate_override_id: str | None = None
    override_reason: str | None = None
    row_gap_m: float = Field(default=0.02, ge=0, le=1.0)
    column_gap_m: float = Field(default=0.02, ge=0, le=1.0)
    max_panels_per_roof: int = Field(default=500, ge=1, le=5000)

    @model_validator(mode="after")
    def require_override_reason(self):
        if self.selected_candidate_override_id and not (self.override_reason and self.override_reason.strip()):
            raise ValueError("selected_candidate_override_id requires override_reason")
        return self


class PanelModelRead(BaseModel):
    panel_model_id: str
    product_id: str | None = None
    title: str
    manufacturer: str | None = None
    length_m: float
    width_m: float
    power_stc_w: float
    source_quality: str
    design_ready: bool = False
    source: str




class ReviewedPanelModelsRead(BaseModel):
    status: str
    models: list[PanelModelRead] = Field(default_factory=list)
    excluded_count: int = 0
    validation_report: ValidationReport
    summary: dict = Field(default_factory=dict)

class PanelPlacementRead(BaseModel):
    placement_id: str
    roof_plane_id: str
    panel_model_id: str
    product_id: str | None = None
    orientation: PanelPackingOrientation
    power_stc_w: float
    width_m: float
    height_m: float
    centre_local_m: list[float]
    polygon_local_m: list[list[float]]
    rotation_deg: float = 0
    row_index: int | None = None
    column_index: int | None = None


class PanelPackingResultRead(BaseModel):
    project_id: str
    status: str
    design_status: str
    calculation_run_id: str
    input_snapshot_hash_sha256: str
    output_hash_sha256: str
    geometry_quality_report_hash_sha256: str
    validation_report: ValidationReport
    panel_models: list[PanelModelRead] = Field(default_factory=list)
    placements: list[PanelPlacementRead] = Field(default_factory=list)
    candidate_summaries: list[dict] = Field(default_factory=list)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    panel_placements_geojson: dict | None = None
    summary: dict = Field(default_factory=dict)


class PanelPackingCandidateExportRead(BaseModel):
    project_id: str
    calculation_run_id: str
    export_kind: str = "arraylab_panel_packing_candidate_export_v0"
    input_snapshot_hash_sha256: str
    output_hash_sha256: str
    candidate_comparison_hash_sha256: str | None = None
    selected_candidate_ids: list[str] = Field(default_factory=list)
    candidate_summaries: list[dict] = Field(default_factory=list)
    placements: list[dict] = Field(default_factory=list)
    manual_override_record: dict | None = None
    truth_boundary: str


class PanelPackingSelfCheckRead(BaseModel):
    status: str
    project_id: str
    preview_panels_fit: bool
    final_blocks_dev_fallback: bool
    invalid_geometry_blocked: bool
    roof_aligned_candidate_differs: bool = False
    score_mode_changes_selection: bool = False
    mixed_candidate_generated: bool = False
    manual_override_recorded: bool = False
    impossible_override_blocked: bool = False
    aesthetic_score_present: bool = False
    calculation_run_id: str | None = None
    output_hash_sha256: str | None = None


class PanelPackingOverrideIntendedUse(StrEnum):
    preview = "preview"
    final = "final"


class PanelPackingOverrideCreate(BaseModel):
    selected_candidate_id: str = Field(min_length=1)
    override_reason: str = Field(min_length=8, max_length=1000)
    reviewer: str = Field(min_length=2, max_length=120)
    reviewer_role: str = Field(min_length=2, max_length=120)
    intended_use: PanelPackingOverrideIntendedUse = PanelPackingOverrideIntendedUse.preview


class PanelPackingOverrideRead(BaseModel):
    override_id: str
    project_id: str
    calculation_run_id: str
    selected_candidate_id: str
    selected_candidate_hash_sha256: str
    selected_layout_export_hash_sha256: str
    intended_use: str
    reviewer: str
    reviewer_role: str
    override_reason: str
    override_payload: dict = Field(default_factory=dict)
    created_at: str | None = None


class PanelPackingOverrideHistoryRead(BaseModel):
    project_id: str
    override_count: int
    overrides: list[PanelPackingOverrideRead] = Field(default_factory=list)
    truth_boundary: str = "append-only panel packing override history; does not bypass product, structural, electrical, or manufacturer sign-off gates"


class SelectedPanelLayoutExportRead(BaseModel):
    project_id: str
    calculation_run_id: str
    export_kind: str = "arraylab_selected_panel_layout_export_v0"
    input_snapshot_hash_sha256: str
    output_hash_sha256: str
    selected_candidate_ids: list[str] = Field(default_factory=list)
    latest_override: PanelPackingOverrideRead | None = None
    placements: list[dict] = Field(default_factory=list)
    panel_placements_geojson: dict | None = None
    row_annotations: list[dict] = Field(default_factory=list)
    access_corridor_placeholders: list[dict] = Field(default_factory=list)
    downstream_contracts: dict = Field(default_factory=dict)
    selected_layout_export_hash_sha256: str
    truth_boundary: str = "layout export is pre-design evidence for future yield/stringing/BOM; not final structural/electrical approval"


class PanelLayoutEditContractRead(BaseModel):
    project_id: str
    contract_version: str = "panel_layout_edit_contract_v0"
    allowed_actions: list[str] = Field(default_factory=list)
    required_fields_by_action: dict = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    truth_boundary: str = "edit/delete contracts define future UI actions only; edited layouts will need new calculation evidence"


class PanelPackingGovernanceSelfCheckRead(BaseModel):
    status: str
    project_id: str
    override_history_immutable: bool
    final_override_blocked_from_preview_fallback: bool
    selected_layout_export_hash_stable: bool
    layout_edit_contract_available: bool
    override_count: int
    selected_layout_export_hash_sha256: str | None = None
