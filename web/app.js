const mapCanvas = document.querySelector("#mapCanvas");
const mapCtx = mapCanvas.getContext("2d");
const terrainCanvas = document.querySelector("#terrainCanvas");
const terrainCtx = terrainCanvas.getContext("2d");

const state = {
  polygon: [],
  assets: [],
  mapTool: "draw",
  dragVertexIndex: null,
  dragMoved: false,
  appMode: "plan",
  activeView: "setup",
  simulationRadiusM: 5000,
  ignitionDistanceM: 4500,
  windSpeedMps: 8,
  windDirectionDeg: 247,
  humidityPct: 24,
  tempF: 83,
  elevationM: null,
  locationName: "",
  feedLive: false,
  spreadMode: "baseline",
  protectionMode: "balanced",
  cropType: "hay",
  harvestWindow: "now",
  livestockCount: "none",
  irrigationStatus: "available",
  crewSize: 6,
  overlays: {
    fuel: true,
    slope: true,
    wind: true,
    edge: true,
    history: false,
  },
  optimizationGoal: "minimize_firebreak_length",
  successCondition: "no_burn_inside_polygon",
  baseline: null,
  optimization: null,
  runState: "Draft",
  simProgress: 0,
  simRunning: false,
  simStartedAt: 0,
};

const center = { lon: -116.945, lat: 33.035 };
const metersPerPixel = 12;
const simDurationMs = 7800;

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
  locationSearch: document.querySelector("#locationSearch"),
  liveStatus: document.querySelector("#liveStatus"),
  locationSourceText: document.querySelector("#locationSourceText"),
  optimizationGoal: document.querySelector("#optimizationGoal"),
  successCondition: document.querySelector("#successCondition"),
  cropType: document.querySelector("#cropType"),
  harvestWindow: document.querySelector("#harvestWindow"),
  livestockCount: document.querySelector("#livestockCount"),
  irrigationStatus: document.querySelector("#irrigationStatus"),
  crewSize: document.querySelector("#crewSize"),
  crewSizeValue: document.querySelector("#crewSizeValue"),
  baselineFailed: document.querySelector("#baselineFailed"),
  baselineBurned: document.querySelector("#baselineBurned"),
  baselineFlame: document.querySelector("#baselineFlame"),
  currentRiskScore: document.querySelector("#currentRiskScore"),
  improvedRiskScore: document.querySelector("#improvedRiskScore"),
  riskReductionPct: document.querySelector("#riskReductionPct"),
  landAtRiskPct: document.querySelector("#landAtRiskPct"),
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
  simClock: document.querySelector("#simClock"),
  simNarrative: document.querySelector("#simNarrative"),
  simProgress: document.querySelector("#simProgress"),
  windSourceText: document.querySelector("#windSourceText"),
  feedWind: document.querySelector("#feedWind"),
  feedHumidity: document.querySelector("#feedHumidity"),
  feedTemp: document.querySelector("#feedTemp"),
  feedFuel: document.querySelector("#feedFuel"),
  feedElevation: document.querySelector("#feedElevation"),
  feedRisk: document.querySelector("#feedRisk"),
  buildList: document.querySelector("#buildList"),
  recommendationList: document.querySelector("#recommendationList"),
  alertList: document.querySelector("#alertList"),
  forecastList: document.querySelector("#forecastList"),
  assetType: document.querySelector("#assetType"),
  assetHint: document.querySelector("#assetHint"),
  protectionMode: document.querySelector("#protectionMode"),
};

function resizeCanvas(canvas, ctx, minWidth, minHeight) {
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(minWidth, Math.floor(rect.width * ratio));
  canvas.height = Math.max(minHeight, Math.floor(rect.height * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}

function resizeAll() {
  resizeCanvas(mapCanvas, mapCtx, 900, 540);
  resizeCanvas(terrainCanvas, terrainCtx, 760, 420);
  drawAll();
}

function project(point) {
  const mPerDegLat = 111_320;
  const mPerDegLon = Math.cos((center.lat * Math.PI) / 180) * mPerDegLat;
  const xMeters = (point.lon - center.lon) * mPerDegLon;
  const yMeters = (center.lat - point.lat) * mPerDegLat;
  const rect = mapCanvas.getBoundingClientRect();
  return {
    x: rect.width / 2 + xMeters / metersPerPixel,
    y: rect.height / 2 + yMeters / metersPerPixel,
  };
}

function unproject(pixel) {
  const rect = mapCanvas.getBoundingClientRect();
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

function windDirectionLabel() {
  const dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const index = Math.round(state.windDirectionDeg / 45) % dirs.length;
  return dirs[index];
}

function previewHumidity() {
  return state.humidityPct;
}

function previewTempF() {
  return state.tempF;
}

function wildfireRiskScore() {
  const wind = Math.min(1, state.windSpeedMps / 18);
  const dry = Math.min(1, (55 - state.humidityPct) / 45);
  const heat = Math.min(1, (state.tempF - 65) / 35);
  const terrain = state.elevationM == null ? 0.34 : Math.min(0.8, Math.max(0.1, state.elevationM / 2200));
  return Math.max(0, Math.min(100, Math.round((wind * 0.36 + dry * 0.3 + heat * 0.2 + terrain * 0.14) * 100)));
}

function riskLabel() {
  const score = wildfireRiskScore();
  if (score >= 72) return "Very high";
  if (score >= 55) return "High";
  if (score >= 35) return "Moderate";
  return "Lower";
}

function protectionProfile() {
  return {
    low: {
      name: "Low Disruption",
      reduction: 0.28,
      disruption: "Low",
      effort: "Low",
      multiplier: 0.72,
      language: "small fixes that keep crop layout mostly unchanged",
    },
    balanced: {
      name: "Balanced Protection",
      reduction: 0.43,
      disruption: "Medium",
      effort: "Medium",
      multiplier: 1,
      language: "moderate changes around the most exposed edges",
    },
    maximum: {
      name: "Maximum Protection",
      reduction: 0.62,
      disruption: "High",
      effort: "High",
      multiplier: 1.35,
      language: "stronger protection even where it disrupts some operations",
    },
  }[state.protectionMode];
}

function currentRiskScore() {
  const assetPressure = Math.min(14, state.assets.length * 1.7);
  return Math.min(100, Math.round(wildfireRiskScore() + assetPressure));
}

function improvedRiskScore() {
  const score = currentRiskScore();
  return Math.max(5, Math.round(score * (1 - protectionProfile().reduction)));
}

function riskReductionPct() {
  const current = currentRiskScore();
  if (!current) return 0;
  return Math.round(((current - improvedRiskScore()) / current) * 100);
}

function landAtRiskPct() {
  if (state.polygon.length < 3) return 0;
  const base = 18 + wildfireRiskScore() * 0.62 + Math.min(12, state.assets.length * 1.2);
  const adjusted = state.spreadMode === "protected" ? base * (1 - protectionProfile().reduction) : base;
  return Math.max(3, Math.min(92, Math.round(adjusted)));
}

function landAtRiskM2() {
  return Math.round(polygonAreaM2(state.polygon) * (landAtRiskPct() / 100));
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
    (acc, point) => ({
      lon: acc.lon + point.lon / points.length,
      lat: acc.lat + point.lat / points.length,
    }),
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
  const ring = state.polygon.map((point) => [
    Number(point.lon.toFixed(6)),
    Number(point.lat.toFixed(6)),
  ]);
  if (ring.length > 0) {
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) ring.push([...first]);
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

function drawMapBackground(rect) {
  const g = mapCtx.createLinearGradient(0, 0, rect.width, rect.height);
  g.addColorStop(0, "#172115");
  g.addColorStop(0.48, "#28371e");
  g.addColorStop(1, "#101312");
  mapCtx.fillStyle = g;
  mapCtx.fillRect(0, 0, rect.width, rect.height);

  mapCtx.save();
  mapCtx.translate(rect.width / 2, rect.height / 2);
  mapCtx.rotate(-0.28);
  for (let row = -15; row <= 15; row += 1) {
    for (let col = -17; col <= 17; col += 1) {
      const hue = 86 + ((row * 7 + col * 13) % 20);
      mapCtx.fillStyle = `hsla(${hue}, 38%, ${24 + ((row + col) % 4) * 3}%, 0.72)`;
      mapCtx.fillRect(col * 86, row * 64, 66, 44);
    }
  }
  mapCtx.strokeStyle = "rgba(255,255,255,0.14)";
  mapCtx.lineWidth = 2;
  for (let i = -18; i <= 18; i += 1) {
    mapCtx.beginPath();
    mapCtx.moveTo(i * 78, -rect.height);
    mapCtx.lineTo(i * 78 + 360, rect.height);
    mapCtx.stroke();
  }
  mapCtx.restore();

  drawCurvedLine(mapCtx, rect, "#3b7188", 18, [
    [0.04, 0.72],
    [0.3, 0.52],
    [0.46, 0.8],
    [0.72, 0.54],
    [0.96, 0.38],
  ]);
  drawCurvedLine(mapCtx, rect, "#525957", 7, [
    [0.02, 0.28],
    [0.28, 0.22],
    [0.7, 0.18],
    [1.02, 0.12],
  ]);

  mapCtx.strokeStyle = "rgba(255,154,61,0.12)";
  mapCtx.lineWidth = 1;
  for (let x = -80; x < rect.width + 80; x += 42) {
    mapCtx.beginPath();
    mapCtx.moveTo(x, 0);
    mapCtx.lineTo(x + 180, rect.height);
    mapCtx.stroke();
  }
}

function drawCurvedLine(ctx, rect, color, width, points) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.beginPath();
  points.forEach(([x, y], idx) => {
    const px = x * rect.width;
    const py = y * rect.height;
    if (idx === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();
}

function drawCircle(origin, radiusM, stroke, dash = []) {
  const p = project(origin);
  mapCtx.save();
  mapCtx.setLineDash(dash);
  mapCtx.strokeStyle = stroke;
  mapCtx.lineWidth = 2;
  mapCtx.beginPath();
  mapCtx.arc(p.x, p.y, metersToPixels(radiusM), 0, Math.PI * 2);
  mapCtx.stroke();
  mapCtx.restore();
}

function drawPolygon() {
  if (!state.polygon.length) return;
  const pts = state.polygon.map(project);
  mapCtx.fillStyle = "rgba(72, 213, 151, 0.26)";
  mapCtx.strokeStyle = "#78ffc0";
  mapCtx.lineWidth = 4;
  mapCtx.shadowColor = "rgba(72, 213, 151, 0.55)";
  mapCtx.shadowBlur = 18;
  mapCtx.beginPath();
  pts.forEach((point, idx) => {
    if (idx === 0) mapCtx.moveTo(point.x, point.y);
    else mapCtx.lineTo(point.x, point.y);
  });
  if (pts.length > 2) mapCtx.closePath();
  mapCtx.fill();
  mapCtx.stroke();
  mapCtx.shadowBlur = 0;

  pts.forEach((point, idx) => {
    mapCtx.fillStyle = "#06120d";
    mapCtx.strokeStyle = "#8fffd0";
    mapCtx.lineWidth = 3;
    mapCtx.beginPath();
    mapCtx.arc(point.x, point.y, 8, 0, Math.PI * 2);
    mapCtx.fill();
    mapCtx.stroke();
    mapCtx.fillStyle = "#dfffea";
    mapCtx.font = "800 11px Inter, sans-serif";
    mapCtx.fillText(String(idx + 1), point.x + 12, point.y - 11);
  });
}

function drawIgnitions() {
  if (state.polygon.length < 3) return;
  ignitionPoints().forEach((point) => {
    const p = project(point);
    mapCtx.shadowColor = "rgba(255, 88, 45, 0.9)";
    mapCtx.shadowBlur = 18;
    mapCtx.fillStyle = "#ff3f2e";
    mapCtx.strokeStyle = "#ffe66d";
    mapCtx.lineWidth = 2;
    mapCtx.beginPath();
    mapCtx.arc(p.x, p.y, 7, 0, Math.PI * 2);
    mapCtx.fill();
    mapCtx.stroke();
    mapCtx.shadowBlur = 0;
    mapCtx.fillStyle = "#fff6d2";
    mapCtx.font = "800 11px Inter, sans-serif";
    mapCtx.fillText(`${point.bearing}`, p.x + 10, p.y + 4);
  });
}

function drawFireSpread() {
  if (state.polygon.length < 3 || !["baseline", "firebreak", "compare", "report"].includes(state.activeView)) {
    return;
  }
  const origin = polygonCentroid(state.polygon);
  const protectedMode = state.spreadMode === "protected";
  ignitionPoints().forEach((point, idx) => {
    const start = project(point);
    const end = project(origin);
    const baseProgress = state.simProgress || 0.72;
    const progress = protectedMode && idx < 6 ? Math.min(baseProgress, 0.56) : baseProgress;
    const x = start.x + (end.x - start.x) * progress;
    const y = start.y + (end.y - start.y) * progress;
    const width = protectedMode ? 10 + progress * 13 : 14 + progress * 28 + idx * 0.8;
    const g = mapCtx.createLinearGradient(start.x, start.y, x, y);
    g.addColorStop(0, "rgba(255,154,61,0.05)");
    g.addColorStop(0.62, protectedMode ? "rgba(255,190,69,0.18)" : "rgba(255,93,38,0.28)");
    g.addColorStop(1, protectedMode ? "rgba(255,230,109,0.34)" : "rgba(255,28,28,0.7)");
    mapCtx.strokeStyle = g;
    mapCtx.lineWidth = width;
    mapCtx.lineCap = "round";
    mapCtx.beginPath();
    mapCtx.moveTo(start.x, start.y);
    mapCtx.lineTo(x, y);
    mapCtx.stroke();

    mapCtx.fillStyle = "rgba(255,230,109,0.85)";
    mapCtx.shadowColor = "#ff3f2e";
    mapCtx.shadowBlur = 20;
    mapCtx.beginPath();
    mapCtx.arc(x, y, 5 + progress * 7, 0, Math.PI * 2);
    mapCtx.fill();
    mapCtx.shadowBlur = 0;
  });
}

function syntheticSegments(origin) {
  const bearings = [315, 0, 45];
  return bearings.map((bearing, idx) => {
    const a = pointAtBearing(origin, bearing - 16, 1100 + idx * 120);
    const b = pointAtBearing(origin, bearing + 16, 1100 + idx * 120);
    return {
      segment_id: `break_${bearing}`,
      geometry: [
        [a.lon, a.lat],
        [b.lon, b.lat],
      ],
      length_m: 900 + idx * 140,
      estimated_cost: 12000 + idx * 2100,
    };
  });
}

function drawFirebreaks() {
  const showProtectedSpread = state.activeView === "baseline" && state.spreadMode === "protected";
  if (
    state.polygon.length < 3 ||
    (!showProtectedSpread && !["firebreak", "compare", "report"].includes(state.activeView))
  ) {
    return;
  }
  const origin = polygonCentroid(state.polygon);
  const segments = state.optimization?.firebreak_segments?.length
    ? state.optimization.firebreak_segments
    : syntheticSegments(origin);

  segments.forEach((segment) => {
    const coords = segment.geometry || [];
    if (coords.length < 2) return;
    mapCtx.strokeStyle = "#f5f7fb";
    mapCtx.lineWidth = 7;
    mapCtx.lineCap = "round";
    mapCtx.shadowColor = "rgba(86,182,255,0.7)";
    mapCtx.shadowBlur = 15;
    mapCtx.beginPath();
    coords.forEach(([lon, lat], idx) => {
      const p = project({ lon, lat });
      if (idx === 0) mapCtx.moveTo(p.x, p.y);
      else mapCtx.lineTo(p.x, p.y);
    });
    mapCtx.stroke();
    mapCtx.shadowBlur = 0;
  });
  drawWaterOutlets(origin);
}

function drawWaterOutlets(origin) {
  protectionLayout(origin).outlets.forEach((outlet) => {
    const p = project(outlet.point);
    mapCtx.fillStyle = "#56b6ff";
    mapCtx.strokeStyle = "#f5f7fb";
    mapCtx.lineWidth = 2;
    mapCtx.shadowColor = "rgba(86,182,255,0.8)";
    mapCtx.shadowBlur = 14;
    mapCtx.beginPath();
    mapCtx.arc(p.x, p.y, 8, 0, Math.PI * 2);
    mapCtx.fill();
    mapCtx.stroke();
    mapCtx.shadowBlur = 0;
    mapCtx.fillStyle = "#eaf8ff";
    mapCtx.font = "800 11px Inter, sans-serif";
    mapCtx.fillText(outlet.label, p.x + 12, p.y + 4);
  });
}

function protectionLayout(origin) {
  return {
    outlets: [
      { label: "Tank", point: pointAtBearing(origin, 320, 760), detail: "Primary tank or hydrant near road access" },
      { label: "Pump", point: pointAtBearing(origin, 30, 620), detail: "Portable pump pad upwind of farm structures" },
      { label: "Valve", point: pointAtBearing(origin, 140, 700), detail: "Valve standpipe for hose reach around field edge" },
    ],
    access: [
      "Clear a 4 m equipment lane along the north and west edges.",
      "Place water tank or hydrant on the road-facing corner.",
      "Keep pump pad outside the expected flame path and mark it with reflective posts.",
      "Mow or graze fine fuels inside the farm boundary before red-flag days.",
    ],
  };
}

function drawMap() {
  const rect = mapCanvas.getBoundingClientRect();
  drawMapBackground(rect);
  drawRiskOverlays(rect);
  drawWindOverlay(rect, performance.now());
  if (state.polygon.length) {
    const origin = polygonCentroid(state.polygon);
    drawCircle(origin, state.simulationRadiusM, "rgba(143,255,208,0.68)", [8, 8]);
    drawCircle(origin, state.ignitionDistanceM, "rgba(255,154,61,0.78)", [4, 7]);
  }
  drawFireSpread();
  drawFirebreaks();
  drawPolygon();
  drawAssets();
  drawIgnitions();
}

function drawRiskOverlays(rect) {
  mapCtx.save();
  if (state.overlays.fuel) {
    for (let i = 0; i < 10; i += 1) {
      mapCtx.fillStyle = `rgba(255, ${90 + i * 8}, 40, 0.055)`;
      mapCtx.beginPath();
      mapCtx.ellipse(rect.width * (0.12 + i * 0.082), rect.height * (0.25 + (i % 3) * 0.18), 90, 42, i * 0.3, 0, Math.PI * 2);
      mapCtx.fill();
    }
  }
  if (state.overlays.slope) {
    mapCtx.strokeStyle = "rgba(255,230,109,0.12)";
    mapCtx.lineWidth = 2;
    for (let y = 40; y < rect.height; y += 46) {
      mapCtx.beginPath();
      mapCtx.moveTo(0, y);
      mapCtx.lineTo(rect.width, y + Math.sin(y * 0.03) * 34);
      mapCtx.stroke();
    }
  }
  if (state.overlays.edge) {
    mapCtx.strokeStyle = "rgba(255,63,46,0.34)";
    mapCtx.lineWidth = 18;
    mapCtx.beginPath();
    mapCtx.moveTo(rect.width * 0.02, rect.height * 0.08);
    mapCtx.bezierCurveTo(rect.width * 0.2, rect.height * 0.18, rect.width * 0.22, rect.height * 0.78, rect.width * 0.05, rect.height * 0.96);
    mapCtx.stroke();
  }
  if (state.overlays.history) {
    mapCtx.fillStyle = "rgba(255,63,46,0.12)";
    mapCtx.strokeStyle = "rgba(255,230,109,0.26)";
    mapCtx.lineWidth = 2;
    mapCtx.beginPath();
    mapCtx.ellipse(rect.width * 0.76, rect.height * 0.32, 150, 74, -0.45, 0, Math.PI * 2);
    mapCtx.fill();
    mapCtx.stroke();
  }
  mapCtx.restore();
}

function drawAssets() {
  state.assets.forEach((asset, idx) => {
    const p = project(asset.point);
    const meta = assetMeta(asset.type);
    mapCtx.fillStyle = meta.color;
    mapCtx.strokeStyle = "#f5f7fb";
    mapCtx.lineWidth = 2;
    mapCtx.shadowColor = meta.glow;
    mapCtx.shadowBlur = 12;
    mapCtx.beginPath();
    mapCtx.roundRect(p.x - 11, p.y - 11, 22, 22, 6);
    mapCtx.fill();
    mapCtx.stroke();
    mapCtx.shadowBlur = 0;
    mapCtx.fillStyle = "#07100b";
    mapCtx.font = "900 12px Inter, sans-serif";
    mapCtx.textAlign = "center";
    mapCtx.fillText(meta.icon, p.x, p.y + 4);
    mapCtx.textAlign = "start";
    mapCtx.fillStyle = "#f5f7fb";
    mapCtx.font = "800 11px Inter, sans-serif";
    mapCtx.fillText(`${meta.label} ${idx + 1}`, p.x + 15, p.y + 4);
  });
}

function assetMeta(type) {
  return {
    crop: { label: "Crop", icon: "C", color: "#8fffd0", glow: "rgba(143,255,208,0.6)" },
    high_value_crop: { label: "High value", icon: "H", color: "#ffe66d", glow: "rgba(255,230,109,0.7)" },
    barn: { label: "Barn", icon: "B", color: "#ff9a3d", glow: "rgba(255,154,61,0.7)" },
    equipment: { label: "Storage", icon: "S", color: "#ffb15f", glow: "rgba(255,154,61,0.7)" },
    fence: { label: "Fence", icon: "F", color: "#f5f7fb", glow: "rgba(245,247,251,0.6)" },
    livestock: { label: "Livestock", icon: "L", color: "#c8a4ff", glow: "rgba(200,164,255,0.7)" },
    road: { label: "Road", icon: "R", color: "#9fb4bd", glow: "rgba(159,180,189,0.6)" },
    water: { label: "Water", icon: "W", color: "#56b6ff", glow: "rgba(86,182,255,0.7)" },
  }[type] || { label: "Asset", icon: "A", color: "#8fffd0", glow: "rgba(143,255,208,0.6)" };
}

function drawWindOverlay(rect, time) {
  if (!state.overlays.wind) return;
  const direction = (state.windSpeedMps * 11 + state.simProgress * 90) * (Math.PI / 180);
  const dx = Math.cos(direction) * 34;
  const dy = Math.sin(direction) * 18;
  mapCtx.save();
  mapCtx.strokeStyle = "rgba(86,182,255,0.28)";
  mapCtx.fillStyle = "rgba(143,255,208,0.62)";
  mapCtx.lineWidth = 2;
  for (let i = 0; i < 24; i += 1) {
    const x = ((i * 137 + time * 0.04 * state.windSpeedMps) % (rect.width + 120)) - 60;
    const y = 52 + ((i * 83) % Math.max(120, rect.height - 100));
    mapCtx.beginPath();
    mapCtx.moveTo(x, y);
    mapCtx.lineTo(x + dx, y + dy);
    mapCtx.stroke();
    mapCtx.beginPath();
    mapCtx.arc(x + dx, y + dy, 2.5, 0, Math.PI * 2);
    mapCtx.fill();
  }
  mapCtx.restore();
}

function terrainPoint(x, z, rect, time) {
  const scale = Math.min(rect.width / 950, rect.height / 430);
  const y =
    Math.sin(x * 0.018 + time * 0.0007) * 20 +
    Math.cos(z * 0.02 - time * 0.0005) * 16 +
    Math.sin((x + z) * 0.01) * 18;
  return {
    x: rect.width / 2 + (x - z) * 0.72 * scale,
    y: rect.height * 0.6 + (x + z) * 0.28 * scale - y * scale,
    h: y,
  };
}

function drawTerrain(time) {
  const rect = terrainCanvas.getBoundingClientRect();
  terrainCtx.clearRect(0, 0, rect.width, rect.height);

  const sky = terrainCtx.createLinearGradient(0, 0, 0, rect.height);
  sky.addColorStop(0, "#0a0f13");
  sky.addColorStop(0.55, "#12120e");
  sky.addColorStop(1, "#040504");
  terrainCtx.fillStyle = sky;
  terrainCtx.fillRect(0, 0, rect.width, rect.height);

  const step = 42;
  const extent = 336;
  for (let z = -extent; z < extent; z += step) {
    for (let x = -extent; x < extent; x += step) {
      const p1 = terrainPoint(x, z, rect, time);
      const p2 = terrainPoint(x + step, z, rect, time);
      const p3 = terrainPoint(x + step, z + step, rect, time);
      const p4 = terrainPoint(x, z + step, rect, time);
      const heat = fireHeatAt(x, z);
      const base = 28 + Math.max(-12, Math.min(22, (p1.h + p2.h + p3.h + p4.h) / 5));
      terrainCtx.fillStyle =
        heat > 0
          ? `rgba(255, ${90 + heat * 120}, ${20 + heat * 40}, ${0.24 + heat * 0.5})`
          : `hsl(${92 + base * 0.25}, 32%, ${18 + base * 0.28}%)`;
      terrainCtx.strokeStyle = heat > 0 ? "rgba(255,230,109,0.42)" : "rgba(255,255,255,0.07)";
      terrainCtx.lineWidth = 1;
      terrainCtx.beginPath();
      terrainCtx.moveTo(p1.x, p1.y);
      terrainCtx.lineTo(p2.x, p2.y);
      terrainCtx.lineTo(p3.x, p3.y);
      terrainCtx.lineTo(p4.x, p4.y);
      terrainCtx.closePath();
      terrainCtx.fill();
      terrainCtx.stroke();
    }
  }

  drawTerrainFarm(rect, time);
  drawTerrainFire(time);
  drawTerrainBreaks(rect, time);
  drawTerrainTelemetry(rect, time);
}

function fireHeatAt(x, z) {
  if (state.polygon.length < 3 || state.runState === "Draft") return 0;
  const protectedMode = state.spreadMode === "protected";
  const progress = state.simProgress || 0;
  const angleStep = (Math.PI * 2) / 8;
  let heat = 0;
  for (let i = 0; i < 8; i += 1) {
    const angle = i * angleStep;
    const sx = Math.cos(angle) * 280;
    const sz = Math.sin(angle) * 220;
    const limitedProgress = protectedMode && i < 6 ? Math.min(progress, 0.58) : progress;
    const cx = sx * (1 - limitedProgress);
    const cz = sz * (1 - limitedProgress);
    const d = Math.hypot(x - cx, z - cz);
    const intensity = protectedMode ? 0.46 : 1;
    heat = Math.max(heat, Math.max(0, 1 - d / (38 + limitedProgress * 64)) * intensity);
  }
  return heat;
}

function drawTerrainFarm(rect, time) {
  const farm = [
    terrainPoint(-68, -50, rect, time),
    terrainPoint(74, -46, rect, time),
    terrainPoint(86, 60, rect, time),
    terrainPoint(-72, 64, rect, time),
  ];
  terrainCtx.fillStyle = "rgba(72,213,151,0.24)";
  terrainCtx.strokeStyle = "#8fffd0";
  terrainCtx.lineWidth = 3;
  terrainCtx.shadowColor = "rgba(72,213,151,0.8)";
  terrainCtx.shadowBlur = 18;
  terrainCtx.beginPath();
  farm.forEach((p, idx) => {
    if (idx === 0) terrainCtx.moveTo(p.x, p.y);
    else terrainCtx.lineTo(p.x, p.y);
  });
  terrainCtx.closePath();
  terrainCtx.fill();
  terrainCtx.stroke();
  terrainCtx.shadowBlur = 0;
}

function drawTerrainFire(time) {
  if (state.polygon.length < 3 || state.runState === "Draft") return;
  const rect = terrainCanvas.getBoundingClientRect();
  const progress = state.simProgress || 0.05;
  for (let i = 0; i < 8; i += 1) {
    const angle = (Math.PI * 2 * i) / 8;
    const sx = Math.cos(angle) * 280;
    const sz = Math.sin(angle) * 220;
    const cx = sx * (1 - progress);
    const cz = sz * (1 - progress);
    const p = terrainPoint(cx, cz, rect, time);
    const plume = 28 + progress * 42 + Math.sin(time * 0.006 + i) * 8;
    const g = terrainCtx.createRadialGradient(p.x, p.y, 4, p.x, p.y, plume);
    g.addColorStop(0, "rgba(255,238,112,0.95)");
    g.addColorStop(0.32, "rgba(255,112,42,0.78)");
    g.addColorStop(1, "rgba(255,38,25,0)");
    terrainCtx.fillStyle = g;
    terrainCtx.beginPath();
    terrainCtx.arc(p.x, p.y, plume, 0, Math.PI * 2);
    terrainCtx.fill();

    terrainCtx.strokeStyle = "rgba(255,90,42,0.5)";
    terrainCtx.lineWidth = 3;
    terrainCtx.beginPath();
    terrainCtx.moveTo(p.x, p.y);
    terrainCtx.lineTo(rect.width / 2, rect.height * 0.58);
    terrainCtx.stroke();
  }
}

function drawTerrainBreaks(rect, time) {
  const showProtectedSpread = state.activeView === "baseline" && state.spreadMode === "protected";
  if (!showProtectedSpread && !["firebreak", "compare", "report"].includes(state.activeView)) return;
  const segments = [
    [-150, -42, 150, -36],
    [-128, 44, 124, 54],
    [-96, -100, 104, 96],
  ];
  terrainCtx.strokeStyle = "#f5f7fb";
  terrainCtx.lineWidth = 5;
  terrainCtx.shadowColor = "rgba(86,182,255,0.8)";
  terrainCtx.shadowBlur = 16;
  segments.forEach(([x1, z1, x2, z2]) => {
    const a = terrainPoint(x1, z1, rect, time);
    const b = terrainPoint(x2, z2, rect, time);
    terrainCtx.beginPath();
    terrainCtx.moveTo(a.x, a.y);
    terrainCtx.lineTo(b.x, b.y);
    terrainCtx.stroke();
  });
  terrainCtx.shadowBlur = 0;
  drawTerrainOutlets(rect, time);
}

function drawTerrainOutlets(rect, time) {
  const outlets = [
    [-210, -120, "Tank"],
    [190, -70, "Pump"],
    [125, 118, "Valve"],
  ];
  outlets.forEach(([x, z, label]) => {
    const p = terrainPoint(x, z, rect, time);
    terrainCtx.fillStyle = "#56b6ff";
    terrainCtx.strokeStyle = "#eaf8ff";
    terrainCtx.lineWidth = 2;
    terrainCtx.shadowColor = "rgba(86,182,255,0.8)";
    terrainCtx.shadowBlur = 16;
    terrainCtx.beginPath();
    terrainCtx.arc(p.x, p.y - 8, 9, 0, Math.PI * 2);
    terrainCtx.fill();
    terrainCtx.stroke();
    terrainCtx.shadowBlur = 0;
    terrainCtx.fillStyle = "#eaf8ff";
    terrainCtx.font = "800 11px Inter, sans-serif";
    terrainCtx.fillText(label, p.x + 12, p.y - 4);
  });
}

function drawTerrainTelemetry(rect, time) {
  const mast = terrainPoint(-250, -190, rect, time);
  terrainCtx.strokeStyle = "rgba(143,255,208,0.8)";
  terrainCtx.lineWidth = 3;
  terrainCtx.beginPath();
  terrainCtx.moveTo(mast.x, mast.y);
  terrainCtx.lineTo(mast.x, mast.y - 86);
  terrainCtx.stroke();

  terrainCtx.strokeStyle = "rgba(86,182,255,0.7)";
  terrainCtx.lineWidth = 2;
  for (let i = 0; i < 3; i += 1) {
    terrainCtx.beginPath();
    terrainCtx.arc(mast.x, mast.y - 86, 22 + i * 16 + Math.sin(time * 0.004) * 2, -0.6, 0.9);
    terrainCtx.stroke();
  }

  const windY = mast.y - 105;
  terrainCtx.fillStyle = "rgba(7,10,9,0.72)";
  terrainCtx.strokeStyle = "rgba(255,255,255,0.18)";
  terrainCtx.lineWidth = 1;
  terrainCtx.beginPath();
  terrainCtx.roundRect(mast.x + 16, windY - 18, 118, 36, 8);
  terrainCtx.fill();
  terrainCtx.stroke();
  terrainCtx.fillStyle = "#8fffd0";
  terrainCtx.font = "800 12px Inter, sans-serif";
  terrainCtx.fillText(`${state.windSpeedMps} m/s ${windDirectionLabel()}`, mast.x + 28, windY + 5);
}

function updatePanels() {
  els.jobStatus.textContent = state.runState;
  els.vertexCount.textContent = String(state.polygon.length);
  els.areaEstimate.textContent = formatArea(polygonAreaM2(state.polygon));
  els.payloadPreview.textContent = JSON.stringify(payload(), null, 2);
  if (els.simulationRadiusValue) els.simulationRadiusValue.textContent = formatMeters(state.simulationRadiusM);
  if (els.ignitionDistanceValue) els.ignitionDistanceValue.textContent = formatMeters(state.ignitionDistanceM);
  if (els.windSpeedValue) els.windSpeedValue.textContent = `${state.windSpeedMps} m/s`;
  if (els.crewSizeValue) {
    els.crewSizeValue.textContent = `${state.crewSize} ${state.crewSize === 1 ? "person" : "people"}`;
  }
  els.simProgress.value = state.simProgress;
  els.simClock.textContent = `T+${String(Math.round(state.simProgress * 6)).padStart(2, "0")}:00`;
  if (els.windSourceText) {
    els.windSourceText.textContent =
      state.runState === "Results loaded"
        ? "Imported backend result. Weather and fuels came from the result file."
        : state.feedLive
          ? "Live weather loaded from Open-Meteo current conditions for the entered location."
          : "Manual preview value. Connect a weather station or import backend results for real field data.";
  }
  els.liveStatus.textContent = state.feedLive ? "Live weather connected" : "No live feed connected";
  els.locationSourceText.textContent = state.feedLive
    ? `${state.locationName || "Selected location"} current weather updated from Open-Meteo.`
    : "Enter a location to pull current weather and elevation. The preview updates automatically.";
  if (els.feedWind) els.feedWind.textContent = `${state.windSpeedMps} m/s ${windDirectionLabel()}`;
  if (els.feedHumidity) els.feedHumidity.textContent = `${previewHumidity()}%`;
  if (els.feedTemp) els.feedTemp.textContent = `${previewTempF()} F`;
  if (els.feedFuel) els.feedFuel.textContent = state.polygon.length >= 3 ? "Brush / grass" : "Waiting for farm";
  if (els.feedElevation) {
    els.feedElevation.textContent =
      state.elevationM == null ? "Preview" : `${Math.round(state.elevationM).toLocaleString()} m`;
  }
  if (els.feedRisk) els.feedRisk.textContent = `${riskLabel()} (${wildfireRiskScore()}/100)`;
  els.currentRiskScore.textContent = state.polygon.length >= 3 ? `${currentRiskScore()}/100` : "-";
  els.improvedRiskScore.textContent = state.polygon.length >= 3 ? `${improvedRiskScore()}/100` : "-";
  els.riskReductionPct.textContent = state.polygon.length >= 3 ? `${riskReductionPct()}%` : "-";
  els.landAtRiskPct.textContent = state.polygon.length >= 3 ? `${landAtRiskPct()}%` : "-";

  const baseline = state.baseline || {
    scenarios_failed: state.polygon.length >= 3 ? 6 : null,
    burned_area_inside_patch_m2: state.polygon.length >= 3 ? landAtRiskM2() : null,
    max_flame_length_near_patch_m: 3.4,
  };
  els.baselineFailed.textContent = baseline.scenarios_failed ?? "-";
  els.baselineBurned.textContent =
    baseline.burned_area_inside_patch_m2 == null
      ? "-"
      : `${Math.round(baseline.burned_area_inside_patch_m2).toLocaleString()} m2`;
  els.baselineFlame.textContent = baseline.max_flame_length_near_patch_m
    ? `${baseline.max_flame_length_near_patch_m} m`
    : "-";

  renderScenarios();
  renderOptimization();
  renderBuildList();
  renderForecast();
  renderReport();
  updateGuidance();
}

function renderScenarios() {
  els.scenarioList.innerHTML = "";
  const rows = defaultScenarios();
  rows.forEach((scenario) => {
    const card = document.createElement("article");
    card.className = "scenario-card";
    card.innerHTML = `<strong>${scenario.title}</strong><span>${scenario.detail}</span>`;
    els.scenarioList.append(card);
  });
}

function defaultScenarios() {
  const progress = Math.round(state.simProgress * 100);
  return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"].map((label, idx) => ({
    title: `Test fire ${idx + 1} from ${label}`,
    detail:
      state.runState === "Draft"
        ? "Waiting for simulation"
        : idx < (state.baseline?.scenarios_failed || 6)
          ? `Fire front active, ${progress}% along the risk path`
          : "Lower exposure in this preview",
  }));
}

function renderOptimization() {
  const synthetic = state.polygon.length >= 3 ? syntheticSegments(polygonCentroid(state.polygon)) : [];
  const segments = state.optimization?.firebreak_segments || synthetic;
  const recommended = state.optimization?.recommended_layout_id || (segments.length ? "break_arc_01" : "-");
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
    card.innerHTML = `<strong>${segment.segment_id}</strong><span>${formatMeters(
      segment.length_m || 0,
    )} clearing / ${formatMoney(segment.estimated_cost || 0)} estimated</span>`;
    els.segmentList.append(card);
  });

  els.compareList.innerHTML = "";
  const layouts = state.optimization?.ranked_layouts || [
    { layout_id: "break_arc_01", score: 92, firebreak_length_m: totalLength, estimated_cost: totalCost },
    { layout_id: "short_north_cut", score: 118, firebreak_length_m: totalLength * 0.82, estimated_cost: totalCost * 0.77 },
    { layout_id: "full_rim_clear", score: 136, firebreak_length_m: totalLength * 1.18, estimated_cost: totalCost * 1.12 },
  ];
  layouts.forEach((layout) => {
    const card = document.createElement("article");
    card.className = "compare-card";
    card.innerHTML = `<strong>${layout.layout_id}</strong><span>Score ${Math.round(
      layout.score || 0,
    ).toLocaleString()} / ${formatMeters(layout.firebreak_length_m || 0)} clearing / ${formatMoney(
      layout.estimated_cost || 0,
    )}</span>`;
    els.compareList.append(card);
  });
}

function renderBuildList() {
  if (!els.buildList) return;
  els.buildList.innerHTML = "";
  const origin = state.polygon.length >= 3 ? polygonCentroid(state.polygon) : center;
  const layout = protectionLayout(origin);
  const items = [
    { title: "Firebreak placement", detail: "Build the glowing white arc on the upwind and north/west exposure sides first." },
    ...layout.outlets.map((outlet) => ({ title: outlet.label, detail: outlet.detail })),
    ...layout.access.map((detail, idx) => ({ title: `Field step ${idx + 1}`, detail })),
  ];
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "build-card";
    card.innerHTML = `<strong>${item.title}</strong><span>${item.detail}</span>`;
    els.buildList.append(card);
  });
  renderRecommendations();
  renderAlerts();
}

function renderRecommendations() {
  if (!els.recommendationList) return;
  els.recommendationList.innerHTML = "";
  generateRecommendations().forEach((rec) => {
    const card = document.createElement("article");
    card.className = "recommendation-card";
    card.innerHTML = `
      <strong>${rec.title}</strong>
      <span>${rec.text}</span>
      <small>Risk reduction ${rec.riskReduction}% / disruption ${rec.disruption} / effort ${rec.effort}</small>
    `;
    els.recommendationList.append(card);
  });
}

function generateRecommendations() {
  const profile = protectionProfile();
  const cropLabel = {
    hay: "hay or forage",
    grapes: "grapes",
    orchard: "orchard blocks",
    vegetables: "vegetables",
    grain: "grain",
    mixed: "mixed crops",
  }[state.cropType];
  const hasBarn = state.assets.some((asset) => asset.type === "barn");
  const hasLivestock = state.assets.some((asset) => asset.type === "livestock");
  const hasStorage = state.assets.some((asset) => asset.type === "equipment");
  const highValue = state.assets.some((asset) => asset.type === "high_value_crop");
  const recs = [
    {
      title: operationalPriorityTitle(),
      riskReduction: state.harvestWindow === "now" || state.harvestWindow === "week" ? 26 : 12,
      disruption: state.harvestWindow === "later" ? "Low" : "Medium",
      effort: state.crewSize < 4 ? "High" : "Medium",
      text: operationalPriorityText(cropLabel),
    },
    {
      title: `${profile.name}: north-west firebreak`,
      riskReduction: Math.round(profile.reduction * 100),
      disruption: profile.disruption,
      effort: profile.effort,
      text: `Your northwest boundary borders the mock dense-fuel edge and aligns with ${windDirectionLabel()} wind exposure. Add a maintained firebreak on the glowing edge first. This uses ${profile.language}.`,
    },
    {
      title: "Keep access routes open",
      riskReduction: 18,
      disruption: "Low",
      effort: "Low",
      text: "Keep the road-facing lane clear enough for a water truck or small engine. This protects response access without changing the crop plan.",
    },
    {
      title: "Place water where crews can reach it",
      riskReduction: 22,
      disruption: "Low",
      effort: "Medium",
      text: "Use the Tank, Pump, and Valve points as a practical water layout: one road-facing supply, one pump pad, and one standpipe near the far field edge.",
    },
  ];
  if (hasBarn || hasStorage) {
    recs.push({
      title: "Move storage away from flame paths",
      riskReduction: 16,
      disruption: "Medium",
      effort: "Medium",
      text: "Equipment or barn assets are marked on the map. Move loose storage and fuel cans inward or behind a low-fuel buffer before red-flag days.",
    });
  }
  if (hasLivestock) {
    recs.push({
      title: "Livestock safer holding route",
      riskReduction: 14,
      disruption: "Low",
      effort: "Medium",
      text: "Keep a gate and lane open from the livestock area to the lowest-risk field. Mark this route before smoke or fire crews arrive.",
    });
  }
  if (state.livestockCount !== "none" && !hasLivestock) {
    recs.push({
      title: "Mark livestock holding area",
      riskReduction: 13,
      disruption: "Low",
      effort: "Low",
      text: "Add the livestock area on the map so the advisory can route animals to the safest field before smoke or wind worsens.",
    });
  }
  if (state.irrigationStatus !== "none") {
    recs.push({
      title: "Use irrigation as a readiness action",
      riskReduction: state.irrigationStatus === "available" ? 19 : 11,
      disruption: "Low",
      effort: state.irrigationStatus === "limited" ? "Medium" : "Low",
      text: "Wet the low-fuel buffer near structures and high-value crop edges during the coolest part of the day. This is an operational action, not a crop-layout change.",
    });
  }
  if (highValue) {
    recs.push({
      title: "Protect high-value crop blocks first",
      riskReduction: 20,
      disruption: "Low",
      effort: "Low",
      text: "High-value crop blocks should get the first low-fuel buffer. The goal is to preserve planting plans while reducing exposure on the nearest edge.",
    });
  }
  return recs.sort((a, b) => b.riskReduction - a.riskReduction);
}

function operationalPriorityTitle() {
  if (state.harvestWindow === "now") return "Act today: protect harvest value";
  if (state.harvestWindow === "week") return "This week: adjust harvest and labor timing";
  if (state.livestockCount !== "none") return "Prepare livestock movement early";
  return "Keep operations flexible this week";
}

function operationalPriorityText(cropLabel) {
  if (state.harvestWindow === "now") {
    return `Your ${cropLabel} is ready now. If wind or smoke risk rises, prioritize early harvest on exposed edges before layout work that would interrupt field operations.`;
  }
  if (state.harvestWindow === "week") {
    return `Your ${cropLabel} is near harvest. Move labor to morning windows, keep access routes open, and prepare harvest equipment away from the wildland edge.`;
  }
  if (state.livestockCount !== "none") {
    return "Livestock are present. Identify the safest holding field now and keep gates clear so relocation can happen before smoke reduces visibility.";
  }
  return `For ${cropLabel}, focus on low-disruption readiness: water access, clear lanes, and storage cleanup before high-wind periods.`;
}

function renderForecast() {
  if (!els.forecastList) return;
  els.forecastList.innerHTML = "";
  mockForecastRows().forEach((row) => {
    const card = document.createElement("article");
    card.className = `forecast-card ${row.level}`;
    card.innerHTML = `<strong>${row.day}</strong><span>${row.summary}</span><small>${row.action}</small>`;
    els.forecastList.append(card);
  });
}

function mockForecastRows() {
  const base = wildfireRiskScore();
  const days = ["Today", "Tomorrow", "Day 3", "Day 4", "Day 5", "Day 6", "Day 7"];
  return days.map((day, idx) => {
    const score = Math.max(8, Math.min(96, Math.round(base + Math.sin(idx * 1.4) * 14 + idx * 2)));
    const level = score > 65 ? "hot" : score > 42 ? "watch" : "low";
    return {
      day,
      level,
      summary: `${score}/100 risk, ${Math.max(8, Math.round(state.humidityPct - idx * 1.3))}% humidity, ${(
        state.windSpeedMps +
        idx * 0.5
      ).toFixed(1)} m/s wind`,
      action:
        level === "hot"
          ? "Move outdoor work early, stage water, and prepare livestock/crop protection."
          : level === "watch"
            ? "Keep access lanes clear and check irrigation and equipment storage."
            : "Normal operations with basic readiness checks.",
    };
  });
}

function renderAlerts() {
  if (!els.alertList) return;
  els.alertList.innerHTML = "";
  alertRows().forEach((alert) => {
    const card = document.createElement("article");
    card.className = `alert-card ${alert.level}`;
    card.innerHTML = `<strong>${alert.title}</strong><span>${alert.text}</span>`;
    els.alertList.append(card);
  });
}

function alertRows() {
  const risk = wildfireRiskScore();
  return [
    {
      level: risk > 55 ? "hot" : "watch",
      title: "Smoke-sensitive crop watch",
      text: "Monitor grapes, berries, and leafy crops. If smoke exposure rises, separate high-risk blocks for testing.",
    },
    {
      level: state.humidityPct < 25 ? "hot" : "watch",
      title: "Low humidity readiness",
      text: "Check pumps, tanks, and hose fittings before afternoon winds. Pause spark-producing work if humidity keeps dropping.",
    },
    {
      level: state.windSpeedMps > 8 ? "hot" : "watch",
      title: "Livestock movement window",
      text: "If smoke or wind increases, move livestock early to the safer holding zone instead of waiting for evacuation pressure.",
    },
    {
      level: "watch",
      title: "Outdoor labor guidance",
      text: "Use Alert Mode as a placeholder for AQI/smoke feeds. When connected, it can flag poor-air days and harvest timing changes.",
    },
  ];
}

function renderReport() {
  const data = payload();
  const baseline = state.optimization?.baseline_result || state.baseline || {};
  const recommended = state.optimization?.recommended_layout_id || "break_arc_01";
  const boundaryPoints = Math.max(0, data.protected_polygon.coordinates[0].length - 1);
  els.reportText.value = [
    "AgriShield farm fire planning report",
    "",
    `Farm boundary points: ${boundaryPoints}`,
    `Area checked around farm: ${data.simulation_radius_m} m`,
    `Test fires start: ${data.ignition_distance_m} m from farm center`,
    `Wind speed: ${state.windSpeedMps} m/s`,
    `Humidity: ${state.humidityPct}%`,
    `Temperature: ${state.tempF} F`,
    `Main crop: ${state.cropType}`,
    `Harvest window: ${state.harvestWindow}`,
    `Livestock: ${state.livestockCount}`,
    `Irrigation: ${state.irrigationStatus}`,
    `Crew size: ${state.crewSize}`,
    `Wildfire chance estimate: ${riskLabel()} (${wildfireRiskScore()}/100)`,
    `Current risk score: ${currentRiskScore()}/100`,
    `Improved risk score: ${improvedRiskScore()}/100`,
    `Estimated risk reduction: ${riskReductionPct()}%`,
    `Estimated land at risk: ${landAtRiskPct()}%`,
    `Preparedness mode: ${protectionProfile().name}`,
    `Planning goal: ${readableGoal(state.optimizationGoal)}`,
    "",
    `Risky test fires without added breaks: ${baseline.scenarios_failed ?? "pending"}`,
    `Farm area at risk in preview: ${Math.round(
      baseline.burned_area_inside_patch_m2 || 0,
    ).toLocaleString()} m2`,
    `Recommended firebreak plan: ${recommended}`,
    "",
    "Use imported backend results before making field decisions.",
  ].join("\n");
}

async function loadLiveConditionsByQuery() {
  const query = els.locationSearch.value.trim();
  if (!query) {
    els.locationSourceText.textContent = "Type a town, ZIP, or nearby address first.";
    return;
  }
  setLoadingFeed(`Searching for ${query}`);
  const geoUrl = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(
    query,
  )}&count=1&language=en&format=json`;
  let geo = await fetchJson(geoUrl);
  if (!geo.results?.length && query.includes(" ")) {
    geo = await fetchJson(
      `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(
        query.split(/[,\s]+/)[0],
      )}&count=1&language=en&format=json`,
    );
  }
  const place = geo.results?.[0];
  if (!place) throw new Error("No matching location found");
  await loadLiveConditions(place.latitude, place.longitude, readablePlace(place), place.elevation);
}

async function loadLiveConditions(lat, lon, label, elevationHint = null) {
  setLoadingFeed(`Loading conditions for ${label}`);
  const weatherUrl = new URL("https://api.open-meteo.com/v1/forecast");
  weatherUrl.search = new URLSearchParams({
    latitude: String(lat),
    longitude: String(lon),
    current: [
      "temperature_2m",
      "relative_humidity_2m",
      "wind_speed_10m",
      "wind_direction_10m",
      "wind_gusts_10m",
    ].join(","),
    temperature_unit: "fahrenheit",
    wind_speed_unit: "ms",
    forecast_days: "1",
  });
  const elevationUrl = `https://api.open-meteo.com/v1/elevation?latitude=${lat}&longitude=${lon}`;
  const [weather, elevation] = await Promise.all([
    fetchJson(weatherUrl.toString()),
    fetchJson(elevationUrl),
  ]);
  const current = weather.current || {};
  state.windSpeedMps = Number(current.wind_speed_10m ?? state.windSpeedMps);
  state.windDirectionDeg = Number(current.wind_direction_10m ?? state.windDirectionDeg);
  state.humidityPct = Math.round(Number(current.relative_humidity_2m ?? state.humidityPct));
  state.tempF = Math.round(Number(current.temperature_2m ?? state.tempF));
  state.elevationM = Number(elevation.elevation?.[0] ?? elevationHint ?? state.elevationM);
  state.locationName = label;
  state.feedLive = true;
  if (els.windSpeed) els.windSpeed.value = String(state.windSpeedMps);
  resetForBoundaryEdit();
  updatePanels();
  drawAll();
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Live data request failed: ${response.status}`);
  return response.json();
}

function setLoadingFeed(message) {
  els.liveStatus.textContent = message;
  els.locationSourceText.textContent = "Connecting to live weather and elevation services...";
}

function readablePlace(place) {
  return [place.name, place.admin1, place.country_code].filter(Boolean).join(", ");
}

function useDeviceLocation() {
  if (!navigator.geolocation) {
    els.locationSourceText.textContent = "This browser does not expose device location.";
    return;
  }
  setLoadingFeed("Waiting for device location");
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const { latitude, longitude } = position.coords;
      loadLiveConditions(latitude, longitude, "Current device location").catch(showFeedError);
    },
    () => {
      els.liveStatus.textContent = "Location permission not available";
      els.locationSourceText.textContent = "Type a town or ZIP instead, then press Load live conditions.";
    },
    { enableHighAccuracy: false, timeout: 10000 },
  );
}

function showFeedError(error) {
  state.feedLive = false;
  els.liveStatus.textContent = "Live feed unavailable";
  els.locationSourceText.textContent = "Could not load live conditions. Manual preview values are still usable.";
  console.error(error);
  updatePanels();
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
    els.quickDetail.textContent = `${Math.max(0, 3 - state.polygon.length)} more point${
      3 - state.polygon.length === 1 ? "" : "s"
    } needed before checking risk.`;
    els.mapHint.textContent = "Tap the map to add farm corner points";
    els.simNarrative.textContent = "Press Check risk after drawing a farm boundary.";
    return;
  }

  if (state.simRunning) {
    els.quickTask.textContent = "Simulation running";
    els.quickDetail.textContent = "Fire fronts are moving across terrain toward the protected polygon.";
    els.mapHint.textContent = "Live risk simulation in progress";
    els.simNarrative.textContent = `Preview fire fronts advancing with ${state.windSpeedMps} m/s ${windDirectionLabel()} wind.`;
    return;
  }

  if (state.runState === "Draft") {
    els.quickTask.textContent = "Ready to check fire risk";
    els.quickDetail.textContent = "Press Check risk to run the animated preview.";
    els.mapHint.textContent = "Check risk shows the fire simulation";
    els.simNarrative.textContent = "The 3D view below the map will animate terrain, fire fronts, and farm exposure.";
    return;
  }

  els.quickTask.textContent = "Review the firebreak plan";
  els.quickDetail.textContent = "Risk paths, 3D terrain, firebreaks, options, and report are ready.";
  els.mapHint.textContent = "Use tabs to review risk and firebreaks";
  els.simNarrative.textContent = "Simulation complete. Firebreak view shows the recommended clearing.";
}

function runPreviewSimulation() {
  if (state.polygon.length < 3) {
    els.mapHint.textContent = "Add at least three farm boundary points first";
    updateGuidance();
    return;
  }
  state.runState = "Simulating";
  state.simRunning = true;
  state.simProgress = 0;
  state.simStartedAt = performance.now();
  state.baseline = {
    scenarios_failed: Math.max(1, Math.min(8, Math.round(2 + wildfireRiskScore() / 14))),
    burned_area_inside_patch_m2: landAtRiskM2(),
    max_flame_length_near_patch_m: Number((1.2 + state.windSpeedMps * 0.24 + wildfireRiskScore() / 80).toFixed(1)),
  };
  state.optimization = {
    recommended_layout_id: "break_arc_01",
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
}

function tick(time) {
  if (state.simRunning) {
    state.simProgress = Math.min(1, (time - state.simStartedAt) / simDurationMs);
    if (state.simProgress >= 1) {
      state.simRunning = false;
      state.runState = "Previewed";
      state.simProgress = 1;
    }
    updatePanels();
  }
  drawMap();
  drawTerrain(time);
  requestAnimationFrame(tick);
}

function drawAll() {
  drawMap();
  drawTerrain(performance.now());
}

function setView(view) {
  state.activeView = view;
  if (view === "alerts") state.appMode = "alert";
  if (view !== "alerts") state.appMode = "plan";
  document.querySelector("#planMode").classList.toggle("active", state.appMode === "plan");
  document.querySelector("#alertMode").classList.toggle("active", state.appMode === "alert");
  document
    .querySelectorAll(".tab")
    .forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  document
    .querySelectorAll(".view-panel")
    .forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== view));
  updateGuidance();
  drawAll();
}

function addSamplePolygon() {
  state.polygon = [
    { lon: -116.9532, lat: 33.0392 },
    { lon: -116.9388, lat: 33.0384 },
    { lon: -116.9366, lat: 33.0286 },
    { lon: -116.9511, lat: 33.0269 },
  ];
  const origin = polygonCentroid(state.polygon);
  state.assets = [
    { type: "barn", point: pointAtBearing(origin, 300, 260) },
    { type: "high_value_crop", point: pointAtBearing(origin, 80, 310) },
    { type: "livestock", point: pointAtBearing(origin, 170, 360) },
    { type: "water", point: pointAtBearing(origin, 325, 520) },
    { type: "road", point: pointAtBearing(origin, 250, 470) },
  ];
  state.runState = "Draft";
  state.simRunning = false;
  state.simProgress = 0;
  state.baseline = null;
  state.optimization = null;
  updatePanels();
  drawAll();
}

function undoPoint() {
  if (!state.polygon.length) return;
  state.polygon.pop();
  state.baseline = null;
  state.optimization = null;
  state.runState = "Draft";
  state.simRunning = false;
  state.simProgress = 0;
  updatePanels();
  drawAll();
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

function resetForBoundaryEdit() {
  state.runState = "Draft";
  state.simRunning = false;
  state.simProgress = 0;
  state.baseline = null;
  state.optimization = null;
}

function canvasPoint(event) {
  const rect = mapCanvas.getBoundingClientRect();
  return {
    pixel: { x: event.clientX - rect.left, y: event.clientY - rect.top },
    geo: unproject({ x: event.clientX - rect.left, y: event.clientY - rect.top }),
  };
}

function nearestVertex(pixel, maxDistance = 16) {
  let nearest = { index: -1, distance: Infinity };
  state.polygon.forEach((point, index) => {
    const p = project(point);
    const distance = Math.hypot(p.x - pixel.x, p.y - pixel.y);
    if (distance < nearest.distance) nearest = { index, distance };
  });
  return nearest.distance <= maxDistance ? nearest.index : -1;
}

function setMapTool(tool) {
  state.mapTool = tool;
  document.querySelectorAll(".tool").forEach((button) => button.classList.remove("active"));
  const activeButton = {
    draw: "#drawMode",
    edit: "#editPointMode",
    delete: "#deletePointMode",
    asset: "#assetMode",
  }[tool];
  if (activeButton) document.querySelector(activeButton).classList.add("active");
  els.mapHint.textContent =
    tool === "edit"
      ? "Drag boundary points to adjust the farm shape"
      : tool === "delete"
        ? "Tap a boundary point to delete it"
        : tool === "asset"
          ? "Tap the map to place the selected farm asset"
          : "Tap the map to add farm corner points";
}

function addAssetAt(point) {
  const type = els.assetType.value;
  state.assets.push({ type, point });
  els.assetHint.textContent = `${assetMeta(type).label} added`;
  resetForBoundaryEdit();
  updatePanels();
  drawAll();
}

function setAppMode(mode) {
  state.appMode = mode;
  document.querySelector("#planMode").classList.toggle("active", mode === "plan");
  document.querySelector("#alertMode").classList.toggle("active", mode === "alert");
  setView(mode === "alert" ? "alerts" : "setup");
}

function bindEvents() {
  window.addEventListener("resize", resizeAll);

  mapCanvas.addEventListener("pointerdown", (event) => {
    const { pixel, geo } = canvasPoint(event);
    const vertexIndex = nearestVertex(pixel);
    if (vertexIndex >= 0 && state.mapTool === "delete") {
      state.polygon.splice(vertexIndex, 1);
      resetForBoundaryEdit();
      updatePanels();
      drawAll();
      return;
    }
    if (vertexIndex >= 0 && (state.mapTool === "edit" || state.mapTool === "draw")) {
      state.dragVertexIndex = vertexIndex;
      state.dragMoved = false;
      mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (state.mapTool === "asset") {
      addAssetAt(geo);
      return;
    }
    if (state.mapTool === "draw") {
      state.polygon.push(geo);
      resetForBoundaryEdit();
      updatePanels();
      drawAll();
    }
  });

  mapCanvas.addEventListener("pointermove", (event) => {
    const { pixel, geo } = canvasPoint(event);
    els.cursorReadout.textContent = `Lon ${geo.lon.toFixed(5)}, Lat ${geo.lat.toFixed(5)}`;
    if (state.dragVertexIndex != null) {
      state.polygon[state.dragVertexIndex] = geo;
      state.dragMoved = true;
      resetForBoundaryEdit();
      updatePanels();
      drawAll();
      return;
    }
    mapCanvas.style.cursor =
      nearestVertex(pixel) >= 0 ? (state.mapTool === "delete" ? "not-allowed" : "grab") : "crosshair";
  });

  mapCanvas.addEventListener("pointerup", (event) => {
    if (state.dragVertexIndex != null) {
      state.dragVertexIndex = null;
      state.dragMoved = false;
      mapCanvas.releasePointerCapture(event.pointerId);
    }
  });

  document.querySelector("#clearPolygon").addEventListener("click", () => {
    state.polygon = [];
    resetForBoundaryEdit();
    updatePanels();
    drawAll();
  });
  document.querySelector("#drawMode").addEventListener("click", () => setMapTool("draw"));
  document.querySelector("#editPointMode").addEventListener("click", () => setMapTool("edit"));
  document.querySelector("#deletePointMode").addEventListener("click", () => setMapTool("delete"));
  document.querySelector("#undoPoint").addEventListener("click", undoPoint);
  document.querySelector("#samplePolygon").addEventListener("click", addSamplePolygon);
  document.querySelector("#assetMode").addEventListener("click", () => setMapTool("asset"));
  document.querySelector("#clearAssets").addEventListener("click", () => {
    state.assets = [];
    resetForBoundaryEdit();
    updatePanels();
    drawAll();
  });
  document.querySelector("#planMode").addEventListener("click", () => setAppMode("plan"));
  document.querySelector("#alertMode").addEventListener("click", () => setAppMode("alert"));
  document.querySelector("#runSimulation").addEventListener("click", runPreviewSimulation);
  document.querySelector("#loadLiveConditions").addEventListener("click", () => {
    loadLiveConditionsByQuery().catch(showFeedError);
  });
  document.querySelector("#useDeviceLocation").addEventListener("click", useDeviceLocation);
  document.querySelector("#showBaselineSpread").addEventListener("click", () => setSpreadMode("baseline"));
  document.querySelector("#showProtectedSpread").addEventListener("click", () => setSpreadMode("protected"));
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
    state.simProgress = 1;
    setView("firebreak");
    updatePanels();
    drawAll();
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => setView(tab.dataset.view));
  });

  [
    ["simulationRadius", "simulationRadiusM", Number],
    ["ignitionDistance", "ignitionDistanceM", Number],
    ["windSpeed", "windSpeedMps", Number],
    ["optimizationGoal", "optimizationGoal", String],
    ["successCondition", "successCondition", String],
    ["protectionMode", "protectionMode", String],
    ["cropType", "cropType", String],
    ["harvestWindow", "harvestWindow", String],
    ["livestockCount", "livestockCount", String],
    ["irrigationStatus", "irrigationStatus", String],
    ["crewSize", "crewSize", Number],
  ].forEach(([id, key, caster]) => {
    if (!els[id]) return;
    els[id].addEventListener("input", (event) => {
      state[key] = caster(event.target.value);
      if (state.ignitionDistanceM >= state.simulationRadiusM) {
        state.ignitionDistanceM = state.simulationRadiusM - 250;
        els.ignitionDistance.value = String(state.ignitionDistanceM);
      }
      if (["simulationRadiusM", "ignitionDistanceM", "windSpeedMps"].includes(key)) {
        resetForBoundaryEdit();
      }
      updatePanels();
      drawAll();
    });
  });

  [
    ["overlayFuel", "fuel"],
    ["overlaySlope", "slope"],
    ["overlayWind", "wind"],
    ["overlayEdge", "edge"],
    ["overlayHistory", "history"],
  ].forEach(([id, key]) => {
    document.querySelector(`#${id}`).addEventListener("change", (event) => {
      state.overlays[key] = event.target.checked;
      drawAll();
    });
  });
}

function setSpreadMode(mode) {
  state.spreadMode = mode;
  document.querySelector("#showBaselineSpread").classList.toggle("active", mode === "baseline");
  document.querySelector("#showProtectedSpread").classList.toggle("active", mode === "protected");
  if (state.runState === "Draft" && state.polygon.length >= 3) runPreviewSimulation();
  else drawAll();
}

bindEvents();
addSamplePolygon();
resizeAll();
requestAnimationFrame(tick);
