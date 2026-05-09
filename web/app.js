const canvas = document.querySelector("#mapCanvas");
const ctx = canvas.getContext("2d");

const state = {
  polygon: [],
  activeView: "setup",
  simulationRadiusM: 5000,
  ignitionDistanceM: 4500,
  windSpeedMps: 8,
  optimizationGoal: "minimize_firebreak_length",
  successCondition: "no_burn_inside_polygon",
  baseline: null,
  optimization: null,
  runState: "Draft",
};

const center = { lon: -116.945, lat: 33.035 };
const metersPerPixel = 12;

const els = {
  jobStatus: document.querySelector("#jobStatus"),
  vertexCount: document.querySelector("#vertexCount"),
  areaEstimate: document.querySelector("#areaEstimate"),
  payloadPreview: document.querySelector("#payloadPreview"),
  cursorReadout: document.querySelector("#cursorReadout"),
  mapHint: document.querySelector("#mapHint"),
  simulationRadius: document.querySelector("#simulationRadius"),
  simulationRadiusValue: document.querySelector("#simulationRadiusValue"),
  ignitionDistance: document.querySelector("#ignitionDistance"),
  ignitionDistanceValue: document.querySelector("#ignitionDistanceValue"),
  windSpeed: document.querySelector("#windSpeed"),
  windSpeedValue: document.querySelector("#windSpeedValue"),
  optimizationGoal: document.querySelector("#optimizationGoal"),
  successCondition: document.querySelector("#successCondition"),
  baselineFailed: document.querySelector("#baselineFailed"),
  baselineBurned: document.querySelector("#baselineBurned"),
  baselineFlame: document.querySelector("#baselineFlame"),
  scenarioList: document.querySelector("#scenarioList"),
  recommendedLayout: document.querySelector("#recommendedLayout"),
  segmentCount: document.querySelector("#segmentCount"),
  layoutLength: document.querySelector("#layoutLength"),
  layoutCost: document.querySelector("#layoutCost"),
  segmentList: document.querySelector("#segmentList"),
  compareList: document.querySelector("#compareList"),
  reportText: document.querySelector("#reportText"),
  resultsFile: document.querySelector("#resultsFile"),
  quickTask: document.querySelector("#quickTask"),
  quickDetail: document.querySelector("#quickDetail"),
};

function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(900, Math.floor(rect.width * ratio));
  canvas.height = Math.max(620, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}

function project(point) {
  const mPerDegLat = 111_320;
  const mPerDegLon = Math.cos((center.lat * Math.PI) / 180) * mPerDegLat;
  const xMeters = (point.lon - center.lon) * mPerDegLon;
  const yMeters = (center.lat - point.lat) * mPerDegLat;
  const rect = canvas.getBoundingClientRect();
  return {
    x: rect.width / 2 + xMeters / metersPerPixel,
    y: rect.height / 2 + yMeters / metersPerPixel,
  };
}

function unproject(pixel) {
  const rect = canvas.getBoundingClientRect();
  const mPerDegLat = 111_320;
  const mPerDegLon = Math.cos((center.lat * Math.PI) / 180) * mPerDegLat;
  const xMeters = (pixel.x - rect.width / 2) * metersPerPixel;
  const yMeters = (pixel.y - rect.height / 2) * metersPerPixel;
  return {
    lon: center.lon + xMeters / mPerDegLon,
    lat: center.lat - yMeters / mPerDegLat,
  };
}

function metersToPixels(meters) {
  return meters / metersPerPixel;
}

function formatMeters(value) {
  return `${Math.round(value).toLocaleString()} m`;
}

function formatArea(m2) {
  if (!m2) return "0 ha";
  return `${(m2 / 10_000).toFixed(1)} ha`;
}

function formatMoney(value) {
  return `$${Math.round(value).toLocaleString()}`;
}

function polygonAreaM2(points) {
  if (points.length < 3) return 0;
  const projected = points.map(project);
  let sum = 0;
  for (let i = 0; i < projected.length; i += 1) {
    const a = projected[i];
    const b = projected[(i + 1) % projected.length];
    sum += a.x * b.y - b.x * a.y;
  }
  return Math.abs(sum * metersPerPixel * metersPerPixel * 0.5);
}

function polygonCentroid(points) {
  if (!points.length) return { ...center };
  return points.reduce(
    (acc, point) => ({ lon: acc.lon + point.lon / points.length, lat: acc.lat + point.lat / points.length }),
    { lon: 0, lat: 0 },
  );
}

function pointAtBearing(origin, bearingDeg, distanceM) {
  const theta = ((90 - bearingDeg) * Math.PI) / 180;
  const dx = Math.cos(theta) * distanceM;
  const dy = Math.sin(theta) * distanceM;
  const mPerDegLat = 111_320;
  const mPerDegLon = Math.cos((origin.lat * Math.PI) / 180) * mPerDegLat;
  return {
    lon: origin.lon + dx / mPerDegLon,
    lat: origin.lat + dy / mPerDegLat,
  };
}

function ignitionPoints() {
  const origin = polygonCentroid(state.polygon);
  return [0, 45, 90, 135, 180, 225, 270, 315].map((bearing) => ({
    bearing,
    ...pointAtBearing(origin, bearing, state.ignitionDistanceM),
  }));
}

function payload() {
  const ring = state.polygon.map((point) => [Number(point.lon.toFixed(6)), Number(point.lat.toFixed(6))]);
  if (ring.length > 0) {
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      ring.push([...first]);
    }
  }
  return {
    protected_polygon: {
      type: "Polygon",
      coordinates: [ring],
    },
    simulation_radius_m: state.simulationRadiusM,
    ignition_distance_m: state.ignitionDistanceM,
    cell_size_m: 30,
    crs: "EPSG:5070",
    landfire_version: "LF2023",
  };
}

function drawBackground(rect) {
  ctx.fillStyle = "#dbe6d8";
  ctx.fillRect(0, 0, rect.width, rect.height);

  ctx.save();
  ctx.translate(rect.width / 2, rect.height / 2);
  ctx.rotate(-0.18);
  ctx.fillStyle = "#8cab69";
  for (let row = -16; row <= 16; row += 1) {
    for (let col = -16; col <= 16; col += 1) {
      if ((row + col) % 3 === 0) ctx.fillRect(col * 90, row * 72, 68, 46);
    }
  }
  ctx.strokeStyle = "rgba(255,255,255,0.48)";
  ctx.lineWidth = 3;
  for (let i = -18; i <= 18; i += 1) {
    ctx.beginPath();
    ctx.moveTo(i * 82, -rect.height);
    ctx.lineTo(i * 82 + 380, rect.height);
    ctx.stroke();
  }
  ctx.restore();

  ctx.strokeStyle = "#6aa6ba";
  ctx.lineWidth = 20;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(rect.width * 0.08, rect.height * 0.72);
  ctx.bezierCurveTo(rect.width * 0.3, rect.height * 0.52, rect.width * 0.46, rect.height * 0.8, rect.width * 0.7, rect.height * 0.58);
  ctx.bezierCurveTo(rect.width * 0.82, rect.height * 0.47, rect.width * 0.9, rect.height * 0.5, rect.width * 1.04, rect.height * 0.38);
  ctx.stroke();

  ctx.strokeStyle = "#5c625d";
  ctx.lineWidth = 8;
  ctx.beginPath();
  ctx.moveTo(rect.width * 0.04, rect.height * 0.25);
  ctx.lineTo(rect.width * 0.96, rect.height * 0.18);
  ctx.stroke();
}

function drawCircle(origin, radiusM, stroke, dash = []) {
  const p = project(origin);
  ctx.save();
  ctx.setLineDash(dash);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(p.x, p.y, metersToPixels(radiusM), 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawPolygon() {
  if (!state.polygon.length) return;
  const pts = state.polygon.map(project);
  ctx.fillStyle = "rgba(23, 107, 77, 0.32)";
  ctx.strokeStyle = "#176b4d";
  ctx.lineWidth = 4;
  ctx.beginPath();
  pts.forEach((point, idx) => {
    if (idx === 0) ctx.moveTo(point.x, point.y);
    else ctx.lineTo(point.x, point.y);
  });
  if (pts.length > 2) ctx.closePath();
  ctx.fill();
  ctx.stroke();

  pts.forEach((point, idx) => {
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#176b4d";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(point.x, point.y, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#176b4d";
    ctx.font = "700 11px Inter, sans-serif";
    ctx.fillText(String(idx + 1), point.x + 12, point.y - 11);
  });
}

function drawIgnitions() {
  if (state.polygon.length < 3) return;
  ignitionPoints().forEach((point) => {
    const p = project(point);
    ctx.fillStyle = "#d85b2a";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "#17211b";
    ctx.font = "700 11px Inter, sans-serif";
    ctx.fillText(`${point.bearing}°`, p.x + 10, p.y + 4);
  });
}

function drawFireSpread() {
  if (state.polygon.length < 3 || !["baseline", "compare"].includes(state.activeView)) return;
  const origin = polygonCentroid(state.polygon);
  ignitionPoints().forEach((point, idx) => {
    const start = project(point);
    const end = project(origin);
    ctx.strokeStyle = `rgba(184, 51, 44, ${0.12 + idx * 0.03})`;
    ctx.lineWidth = 30;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
  });
}

function drawFirebreaks() {
  if (state.polygon.length < 3 || !["firebreak", "compare", "report"].includes(state.activeView)) return;
  const origin = polygonCentroid(state.polygon);
  const layouts = state.optimization?.firebreak_segments?.length
    ? state.optimization.firebreak_segments
    : syntheticSegments(origin);

  layouts.forEach((segment) => {
    const coords = segment.geometry || [];
    if (coords.length < 2) return;
    ctx.strokeStyle = "#202428";
    ctx.lineWidth = 7;
    ctx.lineCap = "round";
    ctx.beginPath();
    coords.forEach(([lon, lat], idx) => {
      const p = project({ lon, lat });
      if (idx === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  });
}

function syntheticSegments(origin) {
  const bearings = [315, 0, 45];
  return bearings.map((bearing, idx) => {
    const a = pointAtBearing(origin, bearing - 16, 1100 + idx * 120);
    const b = pointAtBearing(origin, bearing + 16, 1100 + idx * 120);
    return {
      segment_id: `preview_${bearing}`,
      geometry: [
        [a.lon, a.lat],
        [b.lon, b.lat],
      ],
      length_m: 900 + idx * 140,
      estimated_cost: 12000 + idx * 2100,
    };
  });
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  drawBackground(rect);
  if (state.polygon.length) {
    const origin = polygonCentroid(state.polygon);
    drawCircle(origin, state.simulationRadiusM, "rgba(27, 111, 90, 0.7)", [8, 8]);
    drawCircle(origin, state.ignitionDistanceM, "rgba(216, 91, 42, 0.76)", [4, 7]);
  }
  drawFireSpread();
  drawFirebreaks();
  drawPolygon();
  drawIgnitions();
}

function updatePanels() {
  els.jobStatus.textContent = state.runState;
  els.vertexCount.textContent = String(state.polygon.length);
  els.areaEstimate.textContent = formatArea(polygonAreaM2(state.polygon));
  els.payloadPreview.textContent = JSON.stringify(payload(), null, 2);
  updateGuidance();

  els.simulationRadiusValue.textContent = formatMeters(state.simulationRadiusM);
  els.ignitionDistanceValue.textContent = formatMeters(state.ignitionDistanceM);
  els.windSpeedValue.textContent = `${state.windSpeedMps} m/s`;

  const baseline = state.baseline || {
    scenarios_failed: state.polygon.length >= 3 ? 6 : null,
    burned_area_inside_patch_m2: state.polygon.length >= 3 ? Math.round(polygonAreaM2(state.polygon) * 0.62) : null,
    max_flame_length_near_patch_m: 3.4,
  };
  els.baselineFailed.textContent = baseline.scenarios_failed ?? "-";
  els.baselineBurned.textContent =
    baseline.burned_area_inside_patch_m2 == null ? "-" : `${Math.round(baseline.burned_area_inside_patch_m2).toLocaleString()} m²`;
  els.baselineFlame.textContent = baseline.max_flame_length_near_patch_m
    ? `${baseline.max_flame_length_near_patch_m} m`
    : "-";

  renderScenarios();
  renderOptimization();
  renderReport();
}

function renderScenarios() {
  els.scenarioList.innerHTML = "";
  const scenarios = state.optimization?.ranked_layouts?.length ? scenarioRowsFromOptimization() : defaultScenarios();
  scenarios.forEach((scenario) => {
    const card = document.createElement("article");
    card.className = "scenario-card";
    card.innerHTML = `<strong>${scenario.title}</strong><span>${scenario.detail}</span>`;
    els.scenarioList.append(card);
  });
}

function defaultScenarios() {
  return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].map((label, idx) => ({
    title: `Test fire ${idx + 1} from ${label}`,
    detail: idx < 6 ? "Likely to reach the farm without added clearing" : "Lower exposure in this preview",
  }));
}

function scenarioRowsFromOptimization() {
  const baseline = state.optimization.baseline_result || {};
  return [
    {
      title: "Baseline aggregate",
      detail: `${baseline.scenarios_failed ?? "-"} failed scenarios, ${Math.round(baseline.burned_area_inside_patch_m2 || 0).toLocaleString()} m² burned inside patch`,
    },
  ];
}

function renderOptimization() {
  const synthetic = state.polygon.length >= 3 ? syntheticSegments(polygonCentroid(state.polygon)) : [];
  const segments = state.optimization?.firebreak_segments || synthetic;
  const recommended = state.optimization?.recommended_layout_id || (segments.length ? "preview_layout" : "-");
  const totalLength = segments.reduce((sum, segment) => sum + (segment.length_m || 0), 0);
  const totalCost = segments.reduce((sum, segment) => sum + (segment.estimated_cost || 0), 0);

  els.recommendedLayout.textContent = recommended;
  els.segmentCount.textContent = segments.length ? String(segments.length) : "-";
  els.layoutLength.textContent = segments.length ? formatMeters(totalLength) : "-";
  els.layoutCost.textContent = segments.length ? formatMoney(totalCost) : "-";

  els.segmentList.innerHTML = "";
  segments.forEach((segment) => {
    const card = document.createElement("article");
    card.className = "scenario-card";
    card.innerHTML = `<strong>${segment.segment_id}</strong><span>${formatMeters(segment.length_m || 0)} clearing · ${formatMoney(segment.estimated_cost || 0)} estimated</span>`;
    els.segmentList.append(card);
  });

  els.compareList.innerHTML = "";
  const layouts = state.optimization?.ranked_layouts || [
    { layout_id: "preview_A", score: 120, firebreak_length_m: totalLength, estimated_cost: totalCost },
    { layout_id: "preview_B", score: 145, firebreak_length_m: totalLength * 0.82, estimated_cost: totalCost * 0.77 },
    { layout_id: "preview_C", score: 176, firebreak_length_m: totalLength * 1.18, estimated_cost: totalCost * 1.12 },
  ];
  layouts.forEach((layout) => {
    const card = document.createElement("article");
    card.className = "compare-card";
    card.innerHTML = `<strong>${layout.layout_id}</strong><span>Score ${Math.round(layout.score || 0).toLocaleString()} · ${formatMeters(layout.firebreak_length_m || 0)} clearing · ${formatMoney(layout.estimated_cost || 0)}</span>`;
    els.compareList.append(card);
  });
}

function renderReport() {
  const data = payload();
  const baseline = state.optimization?.baseline_result || state.baseline || {};
  const recommended = state.optimization?.recommended_layout_id || "preview_layout";
  const boundaryPoints = Math.max(0, data.protected_polygon.coordinates[0].length - 1);
  els.reportText.value = [
    "AgriShield farm fire planning report",
    "",
    `Farm boundary points: ${boundaryPoints}`,
    `Area checked around farm: ${data.simulation_radius_m} m`,
    `Test fires start: ${data.ignition_distance_m} m from farm center`,
    `Wind speed: ${state.windSpeedMps} m/s`,
    `Planning goal: ${readableGoal(state.optimizationGoal)}`,
    "",
    `Risky test fires without added breaks: ${baseline.scenarios_failed ?? "pending"}`,
    `Farm area at risk in preview: ${Math.round(baseline.burned_area_inside_patch_m2 || 0).toLocaleString()} m²`,
    `Recommended firebreak plan: ${recommended}`,
    "",
    "Use imported backend results before making field decisions.",
  ].join("\n");
}

function readableGoal(goal) {
  return {
    minimize_firebreak_length: "shortest useful firebreak",
    minimize_cost: "lowest estimated cost",
    maximize_risk_reduction: "most protection",
  }[goal] || goal;
}

function updateGuidance() {
  const steps = document.querySelectorAll(".step");
  const activeIndex = state.polygon.length < 3 ? 0 : state.runState === "Draft" ? 1 : 2;
  steps.forEach((step, index) => step.classList.toggle("active", index === activeIndex));

  if (state.polygon.length < 3) {
    els.quickTask.textContent = "Mark your farm boundary";
    els.quickDetail.textContent = `${Math.max(0, 3 - state.polygon.length)} more point${3 - state.polygon.length === 1 ? "" : "s"} needed before checking risk.`;
    els.mapHint.textContent = "Tap the map to add farm corner points";
    return;
  }

  if (state.runState === "Draft") {
    els.quickTask.textContent = "Ready to check fire risk";
    els.quickDetail.textContent = "Your farm boundary is set. Run a preview or export this job for the backend.";
    els.mapHint.textContent = "Tap Check risk to preview fire paths";
    return;
  }

  els.quickTask.textContent = "Review the firebreak plan";
  els.quickDetail.textContent = "Switch tabs to see fire risk, recommended clearing, options, and the report.";
  els.mapHint.textContent = "Use tabs to review risk and firebreaks";
}

function runPreviewSimulation() {
  if (state.polygon.length < 3) {
    els.mapHint.textContent = "Add at least three farm boundary points first";
    updateGuidance();
    return;
  }
  const area = polygonAreaM2(state.polygon);
  state.runState = "Previewed";
  state.baseline = {
    scenarios_failed: Math.max(1, Math.min(8, Math.round(3 + state.windSpeedMps / 3))),
    burned_area_inside_patch_m2: Math.round(area * Math.min(0.9, 0.28 + state.windSpeedMps / 30)),
    max_flame_length_near_patch_m: Number((1.2 + state.windSpeedMps * 0.24).toFixed(1)),
  };
  state.optimization = {
    recommended_layout_id: "preview_layout_01",
    firebreak_segments: syntheticSegments(polygonCentroid(state.polygon)),
    baseline_result: {
      scenarios_failed: state.baseline.scenarios_failed,
      burned_area_inside_patch_m2: state.baseline.burned_area_inside_patch_m2,
    },
    optimized_result: {
      scenarios_failed: 1,
      burned_area_inside_patch_m2: Math.round(state.baseline.burned_area_inside_patch_m2 * 0.08),
    },
  };
  setView("baseline");
  updatePanels();
  draw();
}

function setView(view) {
  state.activeView = view;
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  document.querySelectorAll(".view-panel").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== view));
  updateGuidance();
  draw();
}

function addSamplePolygon() {
  state.polygon = [
    { lon: -116.9532, lat: 33.0392 },
    { lon: -116.9388, lat: 33.0384 },
    { lon: -116.9366, lat: 33.0286 },
    { lon: -116.9511, lat: 33.0269 },
  ];
  state.runState = "Draft";
  updatePanels();
  draw();
}

function undoPoint() {
  if (!state.polygon.length) return;
  state.polygon.pop();
  state.baseline = null;
  state.optimization = null;
  state.runState = "Draft";
  updatePanels();
  draw();
}

function exportJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function saveBackendJob() {
  if (state.polygon.length < 3) {
    els.mapHint.textContent = "Add at least three farm boundary points first";
    updateGuidance();
    return;
  }
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    if (!response.ok) {
      const message = await response.text();
      throw new Error(message || `HTTP ${response.status}`);
    }
    const result = await response.json();
    state.runState = "Job saved";
    els.mapHint.textContent = `Saved backend job: ${result.job_json}`;
    updatePanels();
  } catch (error) {
    els.mapHint.textContent = "Backend save needs web/server.py, or use Download job";
    console.error(error);
  }
}

function bindEvents() {
  window.addEventListener("resize", resizeCanvas);

  canvas.addEventListener("click", (event) => {
    const rect = canvas.getBoundingClientRect();
    const point = unproject({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    state.polygon.push(point);
    state.runState = "Draft";
    updatePanels();
    draw();
  });

  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const point = unproject({ x: event.clientX - rect.left, y: event.clientY - rect.top });
    els.cursorReadout.textContent = `Lon ${point.lon.toFixed(5)}, Lat ${point.lat.toFixed(5)}`;
  });

  document.querySelector("#clearPolygon").addEventListener("click", () => {
    state.polygon = [];
    state.baseline = null;
    state.optimization = null;
    state.runState = "Draft";
    updatePanels();
    draw();
  });
  document.querySelector("#undoPoint").addEventListener("click", undoPoint);
  document.querySelector("#samplePolygon").addEventListener("click", addSamplePolygon);
  document.querySelector("#fitMap")?.addEventListener("click", draw);
  document.querySelector("#runSimulation").addEventListener("click", runPreviewSimulation);
  document.querySelector("#mobileSample").addEventListener("click", addSamplePolygon);
  document.querySelector("#mobileUndo").addEventListener("click", undoPoint);
  document.querySelector("#mobileRun").addEventListener("click", runPreviewSimulation);
  document.querySelector("#saveBackendJob").addEventListener("click", saveBackendJob);
  document.querySelector("#exportJob").addEventListener("click", () => exportJson("agrishield-job.json", payload()));
  document.querySelector("#copyPayload").addEventListener("click", async () => {
    await navigator.clipboard.writeText(JSON.stringify(payload(), null, 2));
    els.mapHint.textContent = "Payload copied";
  });
  document.querySelector("#loadResults").addEventListener("click", () => els.resultsFile.click());
  els.resultsFile.addEventListener("change", async () => {
    const file = els.resultsFile.files?.[0];
    if (!file) return;
    state.optimization = JSON.parse(await file.text());
    state.runState = "Results loaded";
    setView("firebreak");
    updatePanels();
    draw();
  });

  document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));

  [
    ["simulationRadius", "simulationRadiusM", Number],
    ["ignitionDistance", "ignitionDistanceM", Number],
    ["windSpeed", "windSpeedMps", Number],
    ["optimizationGoal", "optimizationGoal", String],
    ["successCondition", "successCondition", String],
  ].forEach(([id, key, caster]) => {
    els[id].addEventListener("input", (event) => {
      state[key] = caster(event.target.value);
      if (state.ignitionDistanceM >= state.simulationRadiusM) {
        state.ignitionDistanceM = state.simulationRadiusM - 250;
        els.ignitionDistance.value = String(state.ignitionDistanceM);
      }
      updatePanels();
      draw();
    });
  });
}

bindEvents();
addSamplePolygon();
resizeCanvas();
