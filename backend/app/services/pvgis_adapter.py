from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.models.db_models import PvgisRequestCache
from app.services.hash_utils import stable_json_hash

PVGIS_ENDPOINT = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
PVGIS_ADAPTER_VERSION = "0.5.2-pvgis-monthly-cache"
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class PvgisAdapterError(Exception):
    pass


@dataclass(frozen=True)
class PvgisFetchResult:
    status_code: int
    payload: dict[str, Any] | None = None
    text: str | None = None
    final_url: str | None = None


PvgisFetcher = Callable[[str, dict[str, Any], float], PvgisFetchResult]


def arraylab_azimuth_to_pvgis_aspect(azimuth_true_deg: float) -> float:
    """Convert true azimuth to PVGIS fixed-system aspect convention.

    ArrayLab stores true azimuth, where 180 = south, 90 = east, 270 = west.
    PVGIS PVcalc fixed-system aspect uses south as 0, east as -90 and west as +90.
    """
    return round(((float(azimuth_true_deg) - 180.0 + 180.0) % 360.0) - 180.0, 6)


def build_pvgis_pvcalc_params(
    *,
    lat: float,
    lon: float,
    peakpower_kwp: float,
    slope_deg: float,
    azimuth_true_deg: float,
    loss_pct: float,
    pvtechchoice: str = "crystSi",
    mountingplace: str = "building",
) -> dict[str, Any]:
    if peakpower_kwp <= 0:
        raise PvgisAdapterError("PVGIS request requires positive peakpower_kwp.")
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise PvgisAdapterError("PVGIS request requires valid latitude/longitude.")
    if not (0 <= slope_deg <= 90):
        raise PvgisAdapterError("PVGIS request requires slope_deg between 0 and 90.")
    params = {
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
        "peakpower": round(float(peakpower_kwp), 4),
        "loss": round(float(loss_pct), 3),
        "angle": round(float(slope_deg), 3),
        "aspect": arraylab_azimuth_to_pvgis_aspect(float(azimuth_true_deg)),
        "pvtechchoice": pvtechchoice,
        "mountingplace": mountingplace,
        "outputformat": "json",
        "browser": 0,
    }
    return params


def pvgis_request_hash(params: dict[str, Any]) -> str:
    return stable_json_hash({"endpoint": PVGIS_ENDPOINT, "params": params, "adapter_version": PVGIS_ADAPTER_VERSION})


def _httpx_fetcher(url: str, params: dict[str, Any], timeout_seconds: float) -> PvgisFetchResult:
    response = httpx.get(url, params=params, timeout=timeout_seconds)
    try:
        payload = response.json()
    except Exception:
        payload = None
    return PvgisFetchResult(status_code=response.status_code, payload=payload, text=response.text[:2000], final_url=str(response.url))


def parse_pvgis_monthly_response(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    """Parse PVGIS PVcalc monthly JSON into ArrayLab's monthly kWh rows.

    Accepts both the normal `outputs.monthly.fixed` shape and a few defensive variants
    used by test fixtures / future API changes. This keeps the adapter conservative:
    unknown shapes fail loudly instead of inventing monthly energy.
    """
    try:
        outputs = payload.get("outputs", {})
        monthly_block = outputs.get("monthly", {})
        if isinstance(monthly_block, dict):
            rows = monthly_block.get("fixed") or monthly_block.get("Fixed") or monthly_block.get("data")
        else:
            rows = monthly_block
        if not isinstance(rows, list) or len(rows) != 12:
            raise ValueError("PVGIS monthly fixed output did not contain exactly 12 rows")
        monthly: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("PVGIS monthly row is not an object")
            raw_month = int(row.get("month", i + 1))
            energy = row.get("E_m")
            if energy is None:
                energy = row.get("E") or row.get("energy") or row.get("kwh")
            if energy is None:
                raise ValueError(f"PVGIS monthly row {i + 1} has no E_m/energy value")
            monthly.append({
                "month": raw_month,
                "month_name": MONTH_NAMES[raw_month - 1] if 1 <= raw_month <= 12 else str(raw_month),
                "kwh": round(float(energy), 3),
                "source_field": "E_m",
                "pvgis_raw": row,
            })
        annual_from_rows = round(sum(row["kwh"] for row in monthly), 3)
        totals = outputs.get("totals", {})
        fixed_total = totals.get("fixed", {}) if isinstance(totals, dict) else {}
        annual = fixed_total.get("E_y") if isinstance(fixed_total, dict) else None
        if annual is None:
            annual = fixed_total.get("energy_yearly") if isinstance(fixed_total, dict) else None
        if annual is None:
            annual = annual_from_rows
        return monthly, round(float(annual), 3)
    except Exception as exc:
        raise PvgisAdapterError(f"Could not parse PVGIS monthly response: {exc}") from exc


def _cache_read(db: Session, request_hash: str) -> PvgisRequestCache | None:
    return db.get(PvgisRequestCache, request_hash)


def get_or_fetch_pvgis_monthly(
    db: Session,
    *,
    params: dict[str, Any],
    allow_network: bool = False,
    force_refresh: bool = False,
    timeout_seconds: float = 10.0,
    fetcher: PvgisFetcher | None = None,
    requested_by: str | None = None,
) -> PvgisRequestCache:
    request_hash = pvgis_request_hash(params)
    existing = _cache_read(db, request_hash)
    if existing is not None and existing.status == "succeeded" and not force_refresh:
        existing.cache_hit_count = (existing.cache_hit_count or 0) + 1
        existing.last_accessed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    if not allow_network and fetcher is None:
        if existing is not None:
            existing.cache_hit_count = (existing.cache_hit_count or 0) + 1
            existing.last_accessed_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(existing)
            return existing
        record = PvgisRequestCache(
            request_hash_sha256=request_hash,
            endpoint=PVGIS_ENDPOINT,
            params=params,
            status="not_fetched_network_disabled",
            requested_by=requested_by,
            adapter_version=PVGIS_ADAPTER_VERSION,
            error_message="PVGIS network fetch is disabled and no cache record exists.",
            url_preview=PVGIS_ENDPOINT + "?" + urlencode(params),
            parsed_monthly=[],
            response_payload=None,
            annual_kwh=None,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    active_fetcher = fetcher or _httpx_fetcher
    record = existing or PvgisRequestCache(
        request_hash_sha256=request_hash,
        endpoint=PVGIS_ENDPOINT,
        params=params,
        status="queued",
        requested_by=requested_by,
        adapter_version=PVGIS_ADAPTER_VERSION,
        url_preview=PVGIS_ENDPOINT + "?" + urlencode(params),
        parsed_monthly=[],
    )
    record.status = "running"
    record.error_message = None
    record.attempt_count = (record.attempt_count or 0) + 1
    record.last_attempted_at = datetime.now(timezone.utc)
    db.add(record)
    db.commit()

    try:
        fetched = active_fetcher(PVGIS_ENDPOINT, params, timeout_seconds)
        record.http_status_code = fetched.status_code
        record.final_url = fetched.final_url
        if fetched.status_code < 200 or fetched.status_code >= 300:
            record.status = "failed"
            record.error_message = f"PVGIS HTTP {fetched.status_code}: {(fetched.text or '')[:500]}"
            record.response_payload = fetched.payload
        elif not fetched.payload:
            record.status = "failed"
            record.error_message = "PVGIS response was not valid JSON."
            record.response_payload = None
        else:
            monthly, annual = parse_pvgis_monthly_response(fetched.payload)
            total = annual or round(sum(row["kwh"] for row in monthly), 3)
            shares = [(row["kwh"] / total if total else 0.0) for row in monthly]
            parsed = []
            for row, share in zip(monthly, shares):
                parsed.append({
                    "month": row["month"],
                    "month_name": row["month_name"],
                    "kwh": row["kwh"],
                    "share_of_annual": round(share, 6),
                    "source_field": row["source_field"],
                })
            record.status = "succeeded"
            record.error_message = None
            record.response_payload = fetched.payload
            record.parsed_monthly = parsed
            record.annual_kwh = round(total, 3)
            record.response_hash_sha256 = stable_json_hash(fetched.payload)
            record.last_accessed_at = datetime.now(timezone.utc)
    except Exception as exc:
        record.status = "failed"
        record.error_message = str(exc)[:1000]
    db.commit()
    db.refresh(record)
    return record


def pvgis_cache_summary(db: Session) -> dict[str, Any]:
    rows = db.query(PvgisRequestCache).all()
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
    return {
        "status": "ok",
        "cache_records": len(rows),
        "by_status": by_status,
        "truth_boundary": "PVGIS cache is evidence for preview yield only until reviewed assumptions, shade, electrical and proposal gates are complete.",
    }
