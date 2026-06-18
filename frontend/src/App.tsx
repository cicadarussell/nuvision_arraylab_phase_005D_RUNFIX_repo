import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

type ApiStatus = {
  current_phase?: string;
  status?: string;
  checks?: Record<string, boolean | string>;
};

type Project = { project_id: string; title: string };
type Point = [number, number];
type PanelModel = {
  panel_model_id: string;
  product_id?: string | null;
  title: string;
  manufacturer?: string | null;
  length_m: number;
  width_m: number;
  power_stc_w: number;
  source_quality: string;
  design_ready: boolean;
  source: string;
};

const API = "http://127.0.0.1:8000/api";
const EX14: Point = [-3.280844, 50.815584];

function closedGeoJson(points: Point[]) {
  const ring = points.length ? [...points, points[0]] : [];
  return {
    type: "Feature",
    properties: { source: "arraylab_maplibre_draft_ui" },
    geometry: { type: "Polygon", coordinates: [ring] },
  };
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${body}`);
  }
  return response.json();
}

export function App() {
  const mapEl = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [status, setStatus] = useState<ApiStatus | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const [roofType, setRoofType] = useState("tiled_pitched");
  const [heightM, setHeightM] = useState("6");
  const [pitchDeg, setPitchDeg] = useState("35");
  const [scoreGoal, setScoreGoal] = useState("max_kwp");
  const [alignment, setAlignment] = useState("roof_azimuth");
  const [designMode, setDesignMode] = useState("preview");
  const [candidateLayoutMode, setCandidateLayoutMode] = useState("all");
  const [candidateOverrideId, setCandidateOverrideId] = useState("");
  const [overrideReason, setOverrideReason] = useState("manual preview override for access/aesthetic review");
  const [panelModels, setPanelModels] = useState<PanelModel[]>([]);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [showDevFallback, setShowDevFallback] = useState(false);
  const [lastPacking, setLastPacking] = useState<any | null>(null);
  const [overrideHistory, setOverrideHistory] = useState<any | null>(null);
  const [selectedLayoutExport, setSelectedLayoutExport] = useState<any | null>(null);
  const [lastYield, setLastYield] = useState<any | null>(null);
  const [solarGeometry, setSolarGeometry] = useState<any | null>(null);
  const [shadePreview, setShadePreview] = useState<any | null>(null);
  const [specificYield, setSpecificYield] = useState("950");
  const [systemLoss, setSystemLoss] = useState("14");
  const [shadeLoss, setShadeLoss] = useState("0");
  const [usePvgisMonthly, setUsePvgisMonthly] = useState(false);
  const [allowPvgisFetch, setAllowPvgisFetch] = useState(false);

  function push(message: string) {
    setLog((old) => [new Date().toLocaleTimeString() + "  " + message, ...old].slice(0, 12));
  }

  useEffect(() => {
    api<ApiStatus>("/debug/restore-check")
      .then((data) => {
        setStatus(data);
        push(`Backend ${data.current_phase || "unknown"} online`);
      })
      .catch((err) => push(`Backend check failed: ${err.message}`));
  }, []);

  useEffect(() => {
    if (!mapEl.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: mapEl.current,
      style: "https://tiles.openfreemap.org/styles/bright",
      center: EX14,
      zoom: 17,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.on("load", () => {
      map.addSource("draft-roof", { type: "geojson", data: closedGeoJson([]) as any });
      map.addLayer({ id: "draft-roof-fill", type: "fill", source: "draft-roof", paint: { "fill-opacity": 0.24 } });
      map.addLayer({ id: "draft-roof-line", type: "line", source: "draft-roof", paint: { "line-width": 3 } });
      map.addSource("packed-panels", { type: "geojson", data: { type: "FeatureCollection", features: [] } as any });
      map.addLayer({ id: "packed-panels-fill", type: "fill", source: "packed-panels", paint: { "fill-opacity": 0.35 } });
      map.addLayer({ id: "packed-panels-line", type: "line", source: "packed-panels", paint: { "line-width": 1.5 } });
    });
    map.on("click", (ev) => {
      setPoints((old) => [...old, [Number(ev.lngLat.lng.toFixed(8)), Number(ev.lngLat.lat.toFixed(8))]]);
    });
    mapRef.current = map;
    return () => map.remove();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getSource("draft-roof")) return;
    const source = map.getSource("draft-roof") as maplibregl.GeoJSONSource;
    source.setData(closedGeoJson(points) as any);
  }, [points]);

  async function createProject() {
    const created = await api<Project>("/projects", {
      method: "POST",
      body: JSON.stringify({ title: "MapLibre roof draft", created_by: "local_frontend" }),
    });
    setProject(created);
    await api(`/projects/${created.project_id}/site`, {
      method: "POST",
      body: JSON.stringify({ postcode: "EX14 3JF", lat: 50.815584, lon: -3.280844, timezone: "Europe/London", source_type: "postcode_lookup", source_confidence: 0.75 }),
    });
    push(`Project created: ${created.project_id}`);
  }

  async function syncRoof() {
    if (!project) throw new Error("Create a project first");
    if (points.length < 3) throw new Error("Draw at least 3 points");
    const result = await api(`/projects/${project.project_id}/geometry/import-geojson-roof`, {
      method: "POST",
      body: JSON.stringify({
        geojson: closedGeoJson(points),
        label: "MapLibre draft roof",
        roof_type: roofType,
        height_m: heightM === "" ? null : Number(heightM),
        pitch_deg: Number(pitchDeg),
        azimuth_deg: 180,
        source_confidence: 0.55,
        created_by: "local_frontend",
      }),
    });
    push(`Roof synced: ${(result as any).roof_plane_id}, validation ${(result as any).validation_status}`);
  }

  async function validate() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/validate-geometry`);
    push(`Validation: ${(result as any).status}, issues: ${((result as any).issues || []).length}`);
  }


  async function qualityReport() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/geometry/quality`);
    const summary = (result as any).summary || {};
    push(`Quality: ${(result as any).status}, usable ${summary.total_usable_area_m2 ?? "?"} m² / original ${summary.total_original_area_m2 ?? "?"} m²`);
  }

  async function packerAllowedArea() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/geometry/packer-allowed-area`);
    push(`Packer export: ${((result as any).payload?.roof_planes || []).length} roof plane(s), status ${(result as any).payload?.validation_status}`);
  }

  async function loadPanelModels() {
    const result = await api(`/catalogue/reviewed-panel-models?include_dev_fallback=${showDevFallback ? "true" : "false"}`);
    setPanelModels((result as any).models || []);
    push(`Panel models: ${((result as any).models || []).length} listed, status ${(result as any).status}`);
  }

  async function runPanelPacking() {
    if (!project) throw new Error("Create a project first");
    const explicitProductIds = selectedProductId ? [selectedProductId] : undefined;
    const allowFallback = !explicitProductIds && designMode === "preview";
    const result = await api(`/projects/${project.project_id}/panel-packing/run`, {
      method: "POST",
      body: JSON.stringify({
        allow_dev_fallback_panels: allowFallback,
        design_mode: designMode,
        panel_product_ids: explicitProductIds,
        packing_alignment: alignment,
        score_goal: scoreGoal,
        candidate_layout_mode: candidateLayoutMode,
        selected_candidate_override_id: candidateOverrideId || undefined,
        override_reason: candidateOverrideId ? overrideReason : undefined,
      }),
    });
    setLastPacking(result as any);
    const map = mapRef.current;
    if (map?.getSource("packed-panels") && (result as any).panel_placements_geojson) {
      (map.getSource("packed-panels") as maplibregl.GeoJSONSource).setData((result as any).panel_placements_geojson);
    }
    const chosen = ((result as any).selected_candidate_ids || []).join(", ");
    push(`Panel packing: ${(result as any).placements?.length || 0} panels, ${(result as any).summary?.total_kwp ?? "?"} kWp, ${scoreGoal}/${alignment}/${candidateLayoutMode}/${designMode}, status ${(result as any).status}`);
    if ((result as any).summary?.candidate_comparison_hash_sha256) push(`Candidate compare hash: ${(result as any).summary.candidate_comparison_hash_sha256.slice(0, 12)}...`);
    if (chosen) push(`Selected candidate: ${chosen}`);
  }

  async function persistSelectedOverride() {
    if (!project) throw new Error("Create a project first");
    if (!lastPacking?.calculation_run_id) throw new Error("Run panel packing first");
    const selected = candidateOverrideId || (lastPacking.selected_candidate_ids || [])[0];
    if (!selected) throw new Error("No selected candidate available");
    const result = await api(`/projects/${project.project_id}/panel-packing/runs/${lastPacking.calculation_run_id}/overrides`, {
      method: "POST",
      body: JSON.stringify({
        selected_candidate_id: selected,
        override_reason: overrideReason,
        reviewer: "local_frontend_user",
        reviewer_role: "designer_test",
        intended_use: designMode === "final" ? "final" : "preview",
      }),
    });
    push(`Override persisted: ${(result as any).override_id} / ${(result as any).selected_candidate_id}`);
  }

  async function loadOverrideHistory() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/panel-packing/overrides`);
    setOverrideHistory(result as any);
    push(`Override history: ${(result as any).override_count || 0} record(s)`);
  }

  async function exportSelectedLayout() {
    if (!project) throw new Error("Create a project first");
    if (!lastPacking?.calculation_run_id) throw new Error("Run panel packing first");
    const result = await api(`/projects/${project.project_id}/panel-packing/runs/${lastPacking.calculation_run_id}/selected-layout-export`);
    setSelectedLayoutExport(result as any);
    push(`Selected layout export: ${((result as any).placements || []).length} panels, hash ${((result as any).selected_layout_export_hash_sha256 || "").slice(0, 12)}...`);
  }

  async function runYieldPreview() {
    if (!project) throw new Error("Create a project first");
    if (!lastPacking?.calculation_run_id) throw new Error("Run panel packing first");
    const result = await api(`/projects/${project.project_id}/yield/preview`, {
      method: "POST",
      body: JSON.stringify({
        selected_layout_calculation_run_id: lastPacking.calculation_run_id,
        assumption_set_id: "UK_ROOF_PREVIEW_V0_1",
        model_tier: usePvgisMonthly ? "T1_pvgis_monthly_cached" : "T0_rough_kwh_per_kwp",
        specific_yield_kwh_per_kwp_year: Number(specificYield),
        system_loss_pct: Number(systemLoss),
        shade_loss_pct: Number(shadeLoss),
        include_pvgis_request_stub: true,
        use_pvgis_monthly: usePvgisMonthly,
        allow_pvgis_network_fetch: allowPvgisFetch,
      }),
    });
    setLastYield(result as any);
    const pvgisState = (result as any).pvgis_cache?.status || "T0 only";
    push(`Yield preview: ${((result as any).annual_kwh_preview || 0).toFixed(0)} kWh/yr from ${((result as any).total_dc_kwp || 0).toFixed(2)} kWp, status ${(result as any).status}, PVGIS ${pvgisState}`);
  }

  async function layoutEditContract() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/panel-packing/layout-edit-contract`);
    push(`Layout edit contract: ${((result as any).allowed_actions || []).length} draft action(s), non-mutating`);
  }

  async function runSolarGeometryDebug() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/yield/solar-geometry-debug`, {
      method: "POST",
      body: JSON.stringify({
        selected_layout_calculation_run_id: lastPacking?.calculation_run_id || undefined,
        sample_day_mode: "monthly_21st",
        sample_hours_local: [9, 12, 15],
        include_shade_engine_contract: true,
      }),
    });
    setSolarGeometry(result as any);
    const first = (result as any).roof_plane_results?.[0];
    push(`Solar geometry: ${(result as any).source_engine}, ${first?.sample_count || 0} samples, mean factor ${first?.mean_beam_plane_factor_vs_horizontal ?? "?"}`);
  }

  async function runShadePreview() {
    if (!project) throw new Error("Create a project first");
    if (!lastPacking?.calculation_run_id) throw new Error("Run panel packing first");
    const result = await api(`/projects/${project.project_id}/shade/preview`, {
      method: "POST",
      body: JSON.stringify({
        selected_layout_calculation_run_id: lastPacking.calculation_run_id,
        sample_day_mode: "seasonal_key_days",
        sample_hours_local: [9, 12, 15],
        sample_grid_x: 3,
        sample_grid_y: 3,
        include_unshaded_samples: false,
      }),
    });
    setShadePreview(result as any);
    push(`Shade preview: ${Math.round(((result as any).shaded_fraction_preview || 0) * 1000) / 10}% sample shade, status ${(result as any).status}, samples ${(result as any).sample_count_total || 0}`);
  }

  async function exportGeoJson() {
    if (!project) throw new Error("Create a project first");
    const result = await api(`/projects/${project.project_id}/geometry/export-geojson`);
    push(`Exported GeoJSON features: ${((result as any).feature_collection?.features || []).length}`);
  }

  function clearDraft() {
    setPoints([]);
    push("Draft polygon cleared");
  }

  return (
    <main className="app">
      <section className="panel hero">
        <p className="eyebrow">NuVision ArrayLab</p>
        <h1>Phase 005D-RUNFIX, shade preview + run reliability</h1>
        <p>This test UI draws a roof polygon, syncs it to the backend, runs geometry quality/setbacks, packs panels, exports a selected layout, and runs a traceable yield estimate. PVGIS has a backend-owned adapter/cache path, the UI can run solar-position debug and first obstruction-shadow preview before annual shade modelling. Browser-side PVGIS calls remain blocked because apparently CORS clown carts are not engineering.</p>
        <div className="status-row">
          <span>Backend: {status?.current_phase || "checking"}</span>
          <span>Project: {project?.project_id || "none"}</span>
          <span>Draft points: {points.length}</span>
        </div>
      </section>

      <section className="layout">
        <div className="panel map-panel">
          <div ref={mapEl} className="map" />
        </div>
        <aside className="panel controls">
          <h2>Roof draft controls</h2>
          <button onClick={() => createProject().catch((err) => push(err.message))}>1. Create test project</button>
          <label>Roof type<select value={roofType} onChange={(e) => setRoofType(e.target.value)}><option value="tiled_pitched">tiled pitched</option><option value="slate">slate</option><option value="trapezoidal_sheet">trapezoidal sheet</option><option value="flat_roof">flat roof</option><option value="ground_mount">ground mount</option><option value="unknown">unknown</option></select></label>
          <label>Height m<input value={heightM} onChange={(e) => setHeightM(e.target.value)} placeholder="required for edge-zone precheck" /></label>
          <label>Pitch deg<input value={pitchDeg} onChange={(e) => setPitchDeg(e.target.value)} /></label>
          <label>Packing alignment<select value={alignment} onChange={(e) => setAlignment(e.target.value)}><option value="roof_azimuth">roof aligned</option><option value="axis_aligned">axis aligned</option></select></label>
          <label>Score goal<select value={scoreGoal} onChange={(e) => setScoreGoal(e.target.value)}><option value="max_kwp">max kWp</option><option value="best_fit">best fit</option><option value="fewer_panels">fewer panels</option><option value="aesthetic">aesthetic rows</option></select></label>
          <label>Candidate layout<select value={candidateLayoutMode} onChange={(e) => setCandidateLayoutMode(e.target.value)}><option value="all">all candidates</option><option value="single_orientation">single orientation only</option><option value="mixed_portrait_landscape">mixed portrait/landscape only</option></select></label>
          <label>Manual candidate override<input value={candidateOverrideId} onChange={(e) => setCandidateOverrideId(e.target.value)} placeholder="candidate_id from comparison table" /></label>
          <label>Override reason<input value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} /></label>
          <label>Design mode<select value={designMode} onChange={(e) => setDesignMode(e.target.value)}><option value="preview">preview only</option><option value="final">final-gated</option></select></label>
          <label>Yield kWh/kWp/yr<input value={specificYield} onChange={(e) => setSpecificYield(e.target.value)} /></label>
          <label>System loss %<input value={systemLoss} onChange={(e) => setSystemLoss(e.target.value)} /></label>
          <label>Shade loss %<input value={shadeLoss} onChange={(e) => setShadeLoss(e.target.value)} /></label>
          <label><input type="checkbox" checked={usePvgisMonthly} onChange={(e) => setUsePvgisMonthly(e.target.checked)} /> use backend PVGIS monthly cache</label>
          <label><input type="checkbox" checked={allowPvgisFetch} onChange={(e) => setAllowPvgisFetch(e.target.checked)} /> allow backend PVGIS fetch if cache missing</label>
          <label><input type="checkbox" checked={showDevFallback} onChange={(e) => setShowDevFallback(e.target.checked)} /> show dev fallback in picker</label>
          <label>Reviewed panel model<select value={selectedProductId} onChange={(e) => setSelectedProductId(e.target.value)}><option value="">auto / fallback preview</option>{panelModels.filter((m) => m.product_id).map((m) => <option key={m.panel_model_id} value={m.product_id || ""}>{m.title} · {m.power_stc_w} W</option>)}</select></label>
          <button onClick={() => syncRoof().catch((err) => push(err.message))}>2. Sync drawn polygon</button>
          <button onClick={() => validate().catch((err) => push(err.message))}>3. Validate geometry</button>
          <button onClick={() => exportGeoJson().catch((err) => push(err.message))}>4. Export GeoJSON</button>
          <button onClick={() => qualityReport().catch((err) => push(err.message))}>5. Run quality + setbacks</button>
          <button onClick={() => packerAllowedArea().catch((err) => push(err.message))}>6. Export packer allowed area</button>
          <button onClick={() => loadPanelModels().catch((err) => push(err.message))}>7. Load reviewed panel models</button>
          <button onClick={() => runPanelPacking().catch((err) => push(err.message))}>8. Run panel packing preview</button>
          <button onClick={() => persistSelectedOverride().catch((err) => push(err.message))}>9. Persist selected override</button>
          <button onClick={() => loadOverrideHistory().catch((err) => push(err.message))}>10. Load override history</button>
          <button onClick={() => exportSelectedLayout().catch((err) => push(err.message))}>11. Export selected layout</button>
          <button onClick={() => runYieldPreview().catch((err) => push(err.message))}>12. Run preview yield</button>
          <button onClick={() => layoutEditContract().catch((err) => push(err.message))}>13. Show edit contract</button>
          <button onClick={() => runSolarGeometryDebug().catch((err) => push(err.message))}>14. Run solar geometry debug</button>
          <button onClick={() => runShadePreview().catch((err) => push(err.message))}>15. Run shade preview</button>
          <button className="secondary" onClick={clearDraft}>Clear draft polygon</button>
          <p className="hint">Click the map 3+ times to draw. The frontend sends RFC 7946 lon/lat GeoJSON; the backend converts it to local metre geometry and stores an evidence snapshot. Blue-ish roof = draft polygon. Panel overlay = backend-generated preview, not final design.</p>
        </aside>
      </section>

      {panelModels.length > 0 && <section className="panel summary">
        <h2>Panel model picker</h2>
        <p className="badge">Final mode accepts only Q3/Q4 reviewed product models. Dev fallback remains preview-only.</p>
        <div className="candidate-table">
          <div className="row head"><span>Model</span><span>Power</span><span>Size</span><span>Source</span></div>
          {panelModels.map((m) => <div className="row" key={m.panel_model_id}><span>{m.title}</span><span>{m.power_stc_w} W</span><span>{m.length_m} × {m.width_m} m</span><span>{m.source_quality}</span></div>)}
        </div>
      </section>}

      {lastPacking && <section className="panel summary">
        <h2>Last packing result</h2>
        <p className="badge">{lastPacking.design_status === "preview_only" ? "PREVIEW ONLY, not final design" : lastPacking.design_status}</p>
        <div className="status-row"><span>{lastPacking.summary?.panel_count || 0} panels</span><span>{lastPacking.summary?.total_kwp || 0} kWp</span><span>{lastPacking.summary?.design_status}</span></div>
        <p className="hint">Selected: {(lastPacking.selected_candidate_ids || []).join(", ") || "none"}</p>
        <p className="hint">Candidates compared: {(lastPacking.candidate_summaries || []).length}</p>
        <p className="hint">Manual override: {lastPacking.summary?.manual_override_record?.candidate_id || "none"}</p>
        <p className="hint">Candidate evidence hash: {lastPacking.summary?.candidate_comparison_hash_sha256 || "none"}</p>
        <div className="candidate-table">
          <div className="row head"><span>Candidate</span><span>Panels</span><span>kWp</span><span>Aesthetic</span><span>Why</span></div>
          {(lastPacking.candidate_summaries || []).slice(0, 8).map((c: any) => <div className={c.selected ? "row selected" : "row"} key={c.candidate_id}><span>{c.orientation} · {c.panel_model_id}</span><span>{c.panel_count}</span><span>{c.total_kwp}</span><span>{c.aesthetic_score ?? "?"}</span><span>{(c.reason_codes || []).join(", ")}</span></div>)}
        </div>
      </section>}

      {overrideHistory && <section className="panel summary">
        <h2>Persistent override history</h2>
        <p className="badge">Append-only. Overrides do not bypass product, structure, electrical, or manufacturer gates.</p>
        <p className="hint">Records: {overrideHistory.override_count}</p>
        <div className="candidate-table">
          <div className="row head"><span>Candidate</span><span>Reviewer</span><span>Role</span><span>Reason</span></div>
          {(overrideHistory.overrides || []).slice(-6).map((o: any) => <div className="row" key={o.override_id}><span>{o.selected_candidate_id}</span><span>{o.reviewer}</span><span>{o.reviewer_role}</span><span>{o.override_reason}</span></div>)}
        </div>
      </section>}

      {selectedLayoutExport && <section className="panel summary">
        <h2>Selected layout export</h2>
        <p className="badge">Pre-design contract for yield/stringing/BOM. Not final approval.</p>
        <div className="status-row"><span>{(selectedLayoutExport.placements || []).length} panels</span><span>{(selectedLayoutExport.row_annotations || []).length} rows</span><span>{selectedLayoutExport.selected_layout_export_hash_sha256?.slice(0, 12)}...</span></div>
        <p className="hint">Yield input: {selectedLayoutExport.downstream_contracts?.yield_input?.status}</p>
        <p className="hint">Stringing input: {selectedLayoutExport.downstream_contracts?.stringing_input?.status}</p>
        <p className="hint">BOM input: {selectedLayoutExport.downstream_contracts?.bom_input?.status}</p>
      </section>}

      {lastYield && <section className="panel summary">
        <h2>Preview yield result</h2>
        <p className="badge">PREVIEW ONLY. Not final proposal, MCS, structural, or electrical approval.</p>
        <div className="status-row"><span>{lastYield.total_dc_kwp} kWp</span><span>{lastYield.annual_kwh_preview} kWh/yr</span><span>{lastYield.specific_yield_kwh_per_kwp_after_losses} kWh/kWp</span></div>
        <p className="hint">Assumption set: {lastYield.assumption_set?.assumption_set_id} · losses {lastYield.assumption_set?.system_loss_pct}% system + {lastYield.assumption_set?.shade_loss_pct}% shade</p>
        <p className="hint">PVGIS stub: {lastYield.pvgis_request_stub?.status || "none"}</p>
        <p className="hint">PVGIS cache: {lastYield.pvgis_cache?.status || "not used"} · {lastYield.pvgis_cache?.request_hash_sha256?.slice(0, 12) || "no hash"}</p>
        <p className="hint">PVGIS/T0 delta: {lastYield.pvgis_comparison?.delta_kwh ?? "n/a"} kWh / {lastYield.pvgis_comparison?.delta_pct ?? "n/a"}%</p>
        <p className="hint">Yield evidence hash: {lastYield.output_hash_sha256?.slice(0, 12)}...</p>
        <div className="candidate-table">
          <div className="row head"><span>Month</span><span>kWh</span><span>Share</span><span></span></div>
          {(lastYield.monthly || []).map((m: any) => <div className="row" key={m.month}><span>{m.month_name}</span><span>{m.kwh}</span><span>{Math.round((m.share_of_annual || 0) * 1000) / 10}%</span><span></span></div>)}
        </div>
      </section>}


      {solarGeometry && <section className="panel summary">
        <h2>Solar geometry debug</h2>
        <p className="badge">PREVIEW DEBUG ONLY. Solar position and incidence sanity checks, not shade/yield finality.</p>
        <div className="status-row"><span>{solarGeometry.source_engine}</span><span>pvlib: {solarGeometry.pvlib_available ? "available" : "fallback"}</span><span>{solarGeometry.output_hash_sha256?.slice(0, 12)}...</span></div>
        <p className="hint">PVGIS note: {(solarGeometry.pvgis_geometry_comparison_notes || [])[0]}</p>
        <p className="hint">Shade contract: {solarGeometry.shade_engine_input_contract?.contract_version || "none"}</p>
        <div className="candidate-table">
          <div className="row head"><span>Roof</span><span>Samples</span><span>Mean factor</span><span>Noon factor</span></div>
          {(solarGeometry.roof_plane_results || []).map((r: any) => <div className="row" key={r.roof_plane_id}><span>{r.roof_label || r.roof_plane_id}</span><span>{r.sample_count}</span><span>{r.mean_beam_plane_factor_vs_horizontal}</span><span>{r.noon_mean_beam_plane_factor_vs_horizontal}</span></div>)}
        </div>
      </section>}

      {shadePreview && <section className="panel summary">
        <h2>Shade preview</h2>
        <p className="badge">PREVIEW DEBUG ONLY. 2D obstruction-shadow sampling, not annual shade-adjusted yield.</p>
        <div className="status-row"><span>{shadePreview.status}</span><span>{Math.round((shadePreview.shaded_fraction_preview || 0) * 1000) / 10}% sample shade</span><span>{shadePreview.shade_result_hash_sha256?.slice(0, 12)}...</span></div>
        <p className="hint">Samples: {shadePreview.sample_count_total} · shaded: {shadePreview.shaded_sample_count_total}</p>
        <p className="hint">Boundary: {shadePreview.truth_boundary}</p>
        <div className="candidate-table">
          <div className="row head"><span>Panel</span><span>Samples</span><span>Shaded</span><span>Worst blockers</span></div>
          {(shadePreview.worst_panels || []).slice(0, 8).map((p: any) => <div className="row" key={p.placement_id}><span>{p.placement_id}</span><span>{p.sample_count}</span><span>{Math.round((p.shaded_fraction || 0) * 1000) / 10}%</span><span>{(p.worst_blocker_ids || []).join(", ") || "none"}</span></div>)}
        </div>
      </section>}

      <section className="panel log">
        <h2>Debug log</h2>
        {log.map((entry, i) => <p key={i}>{entry}</p>)}
      </section>
    </main>
  );
}
