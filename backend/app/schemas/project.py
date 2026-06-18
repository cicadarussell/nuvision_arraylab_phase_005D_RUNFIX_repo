from __future__ import annotations

from app.core.compat import StrEnum
from pydantic import BaseModel, Field, field_validator


class RoofType(StrEnum):
    tiled_pitched = "tiled_pitched"
    slate_pitched = "slate_pitched"
    trapezoidal_sheet = "trapezoidal_sheet"
    corrugated_sheet = "corrugated_sheet"
    standing_seam = "standing_seam"
    flat_roof = "flat_roof"
    ground_mount = "ground_mount"
    unknown = "unknown"


class StructuralTruthState(StrEnum):
    S0_unknown = "S0_unknown"
    S1_precheck_only = "S1_precheck_only"
    S2_manufacturer_calc_required = "S2_manufacturer_calc_required"
    S3_manufacturer_calculated = "S3_manufacturer_calculated"
    S4_engineer_review_required = "S4_engineer_review_required"
    S5_engineer_approved = "S5_engineer_approved"


class RoofPlane(BaseModel):
    roof_plane_id: str
    pitch_deg: float = Field(ge=0, le=75)
    azimuth_deg: float = Field(ge=0, lt=360)
    height_m: float | None = Field(default=None, ge=0)
    roof_type: RoofType = RoofType.unknown
    polygon_local_m: list[list[float]] | None = None

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


class Site(BaseModel):
    postcode: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = "Europe/London"


class ProjectSnapshot(BaseModel):
    project_id: str
    site: Site
    roof_planes: list[RoofPlane] = Field(default_factory=list)
