from app.schemas.project import ProjectSnapshot
from app.services.validation_engine import validate_project_for_mounting_precheck


def test_missing_height_and_unknown_roof_type_block_mounting():
    project = ProjectSnapshot.model_validate({
        "project_id": "test",
        "site": {"postcode": "EX14 4PB"},
        "roof_planes": [{"roof_plane_id": "r1", "pitch_deg": 35, "azimuth_deg": 180, "height_m": None, "roof_type": "unknown"}],
    })
    report = validate_project_for_mounting_precheck(project)
    codes = {i.code for i in report.issues}
    assert report.status == "blocked"
    assert "ROOF_HEIGHT_MISSING" in codes
    assert "ROOF_TYPE_UNKNOWN" in codes


def test_complete_basic_roof_does_not_block_mounting_precheck():
    project = ProjectSnapshot.model_validate({
        "project_id": "test",
        "site": {"postcode": "EX14 4PB"},
        "roof_planes": [{
            "roof_plane_id": "r1", "pitch_deg": 35, "azimuth_deg": 180,
            "height_m": 6.5, "roof_type": "tiled_pitched",
            "polygon_local_m": [[0,0],[8,0],[8,4],[0,4]],
        }],
    })
    report = validate_project_for_mounting_precheck(project)
    assert not report.has_blockers
