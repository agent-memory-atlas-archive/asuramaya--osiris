// NAVIGABLE SPACE, THE RENDERER — piece 2, THE VIEW (thread 71c4ca0d, Thoth DM 10436,
// operator rulings f832c3a4/0a3d6719), now INTEGRATED (decision "NAVIGABLE SPACE,
// INTEGRATION SHAPE", mail 10550): mounted inside /ui/'s own browse stage in place of the
// old #cy cytoscape container, not a separate page. The operator's own repeated corrections
// settled the interaction shape, twice: (1) zoom is LOOKING only, never a data-tier switch —
// navigation is a CLICK; (2) a click doesn't change what's loaded either — "more like click
// to highlight" — every positioned object is drawn AT ONCE (matching 0a3d6719's own original
// wording, "an engine that can handle all objects at once"), and a click only lights the
// clicked object's neighborhood, dims everything else, and opens the inspector. There is no
// tier concept in this file.
//
// Labels: the operator caught a real lag bug — DOM label positions were only recomputed on
// a debounce, so they visibly fell behind the WebGL scene during a drag. Fixed by splitting
// "which nodes are labeled" (nearest-N, genuinely expensive, stays debounced) from "where do
// the ALREADY-CHOSEN labels sit on screen" (cheap — one Vector3.project() per label, no
// resort), which now runs every render frame, not just after panning/zooming settles.
//
// Data source: Khnum's GET /graph/stream (thread b6cb1d7c0b36, wire format frozen by DM
// 10439/10449/10451) — one binary snapshot, decoded client-side (decodeSnapshot below),
// no more client-side tiling/pagination. GET /graph/stream/deltas SSE-polls the outbox for
// incremental moves/retirements after the initial snapshot lands (op:'moved'|'retired').
// Positions and collision avoidance (rings) are entirely Khnum's layout heartbeat's own —
// this module reads x/y as given and never relaxes them client-side.
//
// KNOWN GAP (flagged to Khnum, DM 10554/10555, not blocking): the wire header's `types`/
// `projects` arrays resolve node type_code/project_code, but edge_type_code has no matching
// name array yet — edge color-coding below hashes the raw int until that lands, then swaps
// to real relationship names with no shape change on this side.
//
// Mounts via initSpace(container) rather than running as a page-load IIFE, so console.js
// (the live /ui/ shell) can own the container lifecycle; space.html keeps working as a
// standalone dev harness by calling initSpace() against its own fixed ids.

import * as THREE from "./vendor/three.module.js";

// colour-code an edge by its relationship type — a stable hash-to-hue, since no link-type
// palette exists server-side yet (only /schema's own object_types carry colours). Falls back
// to hashing the raw edge_type_code int until Khnum's edge_types name array lands.
const _edgeColorCache = new Map();
function colorForEdgeType(type) {
  const key = String(type);
  if (_edgeColorCache.has(key)) return _edgeColorCache.get(key);
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  const c = `hsl(${hue}, 55%, 55%)`;
  _edgeColorCache.set(key, c);
  return c;
}

// ---- GET /graph/stream wire decode (a JS twin of graph_stream.decode_snapshot) ---------
// 4-byte LE uint32 header length, that many bytes of UTF-8 JSON header, then the raw arrays
// back to back at the byte offsets the header's own `arrays` map names.
const _DTYPE_CTOR = { f: Float32Array, H: Uint16Array, B: Uint8Array, I: Uint32Array };
function decodeSnapshot(buf) {
  const dv = new DataView(buf);
  const headerLen = dv.getUint32(0, true);
  const headerBytes = new Uint8Array(buf, 4, headerLen);
  const header = JSON.parse(new TextDecoder().decode(headerBytes));
  const bodyStart = 4 + headerLen;
  const out = { ...header, arrays: undefined };
  for (const [name, meta] of Object.entries(header.arrays)) {
    const Ctor = _DTYPE_CTOR[meta.dtype];
    // typed-array views need an offset that's a multiple of their own element size — the
    // wire format packs arrays back to back with no padding, so a Float32/Uint32 view at a
    // non-4-aligned offset throws; slice+copy is the safe general case (arrays here are a
    // few hundred KB at most, not worth hand-padding the server's own byte layout for).
    const byteOff = bodyStart + meta.offset;
    const bytes = buf.slice(byteOff, byteOff + meta.length * Ctor.BYTES_PER_ELEMENT);
    out[name] = new Ctor(bytes);
  }
  return out;
}

async function fetchStreamSnapshot() {
  const buf = await fetch("/graph/stream").then((r) => r.arrayBuffer());
  const snap = decodeSnapshot(buf);
  const nodes = [];
  for (let i = 0; i < snap.count; i++) {
    nodes.push({
      id: snap.object_ids[i],
      type: snap.types[snap.type_code[i]],
      project: snap.projects[snap.project_code[i]],
      x: snap.x[i], y: snap.y[i],
      degree: snap.weight[i],
      statusFlag: snap.status_flag[i],
    });
  }
  const edges = [];
  for (let i = 0; i < snap.edge_count; i++) {
    edges.push({
      source: snap.object_ids[snap.edge_src[i]],
      target: snap.object_ids[snap.edge_dst[i]],
      type: (snap.edge_types && snap.edge_types[snap.edge_type_code[i]]) ?? snap.edge_type_code[i],
    });
  }
  return { nodes, edges };
}

// resolves DOM refs from a passed-in container map, falling back to the same fixed ids
// space.html's own standalone page has always used — lets console.js mount this against
// its own #cy-replacement markup while space.html keeps working unchanged.
function resolveContainer(container) {
  const byId = (id) => document.getElementById(id);
  return {
    wrap: (container && container.wrap) || byId("canvas-wrap"),
    labelsEl: (container && container.labels) || byId("labels"),
    statusEl: (container && container.status) || byId("status-line"),
    levelBadge: (container && container.levelBadge) || byId("graph-level-badge"),
    searchInput: (container && container.searchInput) || byId("graph-search"),
    searchDd: (container && container.searchDd) || byId("graph-search-dd"),
    rightRail: (container && container.rightRail) || byId("right"),
    fitBtn: (container && container.fitBtn) || byId("fit-btn"),
    upBtn: (container && container.upBtn) || byId("up-btn"),
    onFocus: (container && container.onFocus) || null, // (id) => void, shares selection with the table
  };
}

export async function initSpace(container) {
  const { wrap, labelsEl, statusEl, levelBadge, searchInput, searchDd, rightRail, fitBtn, upBtn, onFocus } =
    resolveContainer(container);
  function setStatus(text) { statusEl.textContent = text; }

  const typeColors = await loadTypeColors();

  // ---- renderer / scene / camera -----------------------------------------------------
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  // three.js's ColorManagement converts every hex colour (THREE.Color.set('#8ab4f8')) from
  // sRGB into LINEAR space internally — without this, the renderer displays those linear
  // values as-is, reading systematically darker than the real colour.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  renderer.setSize(wrap.clientWidth, wrap.clientHeight);
  wrap.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1219);
  const pickScene = new THREE.Scene();

  // world extent depends entirely on Khnum's own layout heartbeat (deterministic hash
  // placement, piece A) and is NOT a fixed constant — a project-center hash can land
  // anywhere; fitToNodes() (below) frames the camera from the real loaded bbox instead of
  // a guessed number the moment the first snapshot lands, and Fit re-measures live rather
  // than resetting to a stale guess.
  let viewSize = 1300;
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
  camera.position.set(0, 0, 5);
  camera.lookAt(0, 0, 0);
  function updateFrustum() {
    const a = wrap.clientWidth / wrap.clientHeight;
    camera.left = (-viewSize * a) / 2;
    camera.right = (viewSize * a) / 2;
    camera.top = viewSize / 2;
    camera.bottom = -viewSize / 2;
    camera.updateProjectionMatrix();
  }
  updateFrustum();
  window.addEventListener("resize", () => {
    renderer.setSize(wrap.clientWidth, wrap.clientHeight);
    updateFrustum();
  });

  let mesh = null, pickMesh = null, edgeLines = null;
  let idToNode = [];
  let focusId = null;
  let litIds = new Set();

  function disposeCurrent() {
    for (const m of [mesh, pickMesh, edgeLines]) {
      if (!m) continue;
      scene.remove(m); pickScene.remove(m);
      m.geometry.dispose();
      if (Array.isArray(m.material)) m.material.forEach((x) => x.dispose());
      else m.material.dispose();
    }
    mesh = pickMesh = edgeLines = null;
  }

  // SIZE: a category base (agent > plain object) x a narrow 1x-3x asymptotic multiplier
  // off the object's own live link degree — bounded in SCREEN PIXELS, converted to world
  // units against the current viewSize so a node reads the same visual size across a zoom
  // range (rescaleForZoom, called on every wheel step).
  //
  // The divisor was originally guessed (20) and the operator correctly called it out as
  // not really working — MEASURED against the real distribution instead (48,997 objects):
  // p50=3, p75=4, p90=4, p95=6, p99=23, max=20,295. A divisor of 20 barely moves the curve
  // for the 90% of objects sitting at degree 3-4 (~1.15-1.33x), so almost everything looked
  // the same size. Retuned to 14 so degree=6 (p95, "clearly above average") already reads
  // ~1.6x and true hubs (p99+, 20+) approach the 3x cap, while the P50-P90 bulk (3-4) still
  // stays a modest ~1.3-1.4x — distinguishable without every ordinary node looking inflated.
  const CATEGORY_BASE_PX = { agent: 8, object: 5 };
  const DEGREE_CURVE_DIVISOR = 14;
  function categoryOf(nd) { return nd.type === "Agent" ? "agent" : "object"; }
  function degreeFactor(nd) {
    const signal = nd.degree || 0;
    return 1 + 2 * (1 - 1 / (1 + signal / DEGREE_CURVE_DIVISOR)); // asymptotic 1x -> 3x
  }
  function nodeRadiusPx(nd) { return CATEGORY_BASE_PX[categoryOf(nd)] * degreeFactor(nd); }
  function worldPerPx() { return viewSize / wrap.clientHeight; }

  function buildScene(nodes, edges) {
    disposeCurrent();
    idToNode = nodes;
    const n = nodes.length;
    const geo = new THREE.CircleGeometry(1, 10);
    // this three.js build's fragment shader only multiplies by vColor (and so only shows
    // instanceColor) when USE_COLOR/USE_COLOR_ALPHA is defined, which is driven by a
    // GEOMETRY-level `color` attribute, not instanceColor alone — the vertex shader
    // computes the right colour into vColor but the fragment shader silently drops it
    // without this, rendering flat black regardless of instanceColor.
    geo.setAttribute("color", new THREE.Float32BufferAttribute(
      new Float32Array(geo.attributes.position.count * 3).fill(1), 3));
    const mat = new THREE.MeshBasicMaterial({ vertexColors: true });
    mesh = new THREE.InstancedMesh(geo, mat, Math.max(n, 1));
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const pickMat = new THREE.MeshBasicMaterial({ vertexColors: true });
    pickMesh = new THREE.InstancedMesh(geo, pickMat, Math.max(n, 1));
    pickMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const idColor = new THREE.Color();
    const wpp = worldPerPx();
    for (let i = 0; i < n; i++) {
      const nd = nodes[i];
      nd.radiusPx = nodeRadiusPx(nd);
      // "sizing more intuitive where high-degree nodes stand out without obfuscating
      // smaller nodes" — a bigger circle can still sit BEHIND a smaller one drawn later
      // in the same z-plane; give every node a tiny z bias proportional to its own radius
      // so the important (bigger) ones are always nearer the camera and never occluded.
      dummy.position.set(nd.x || 0, nd.y || 0, nd.radiusPx * 0.002);
      dummy.scale.setScalar(nd.radiusPx * wpp);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      pickMesh.setMatrixAt(i, dummy.matrix);
      color.set(typeColors.get(nd.type) || "#6e7681");
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
      const id = i + 1;
      idColor.setRGB((id & 0xff) / 255, ((id >> 8) & 0xff) / 255, ((id >> 16) & 0xff) / 255);
      pickMesh.instanceColor.setXYZ(i, idColor.r, idColor.g, idColor.b);
    }
    mesh.instanceMatrix.needsUpdate = true;
    pickMesh.instanceMatrix.needsUpdate = true;
    scene.add(mesh);
    pickScene.add(pickMesh);

    const positions = new Float32Array(edges.length * 6);
    const edgeColors = new Float32Array(edges.length * 6);
    let ei = 0, eci = 0;
    const idx = new Map(nodes.map((nd, i) => [nd.id, i]));
    const ec = new THREE.Color();
    for (const e of edges) {
      const a = idx.get(e.source), b = idx.get(e.target);
      if (a == null || b == null) continue;
      const na = nodes[a], nb = nodes[b];
      positions[ei++] = na.x || 0; positions[ei++] = na.y || 0; positions[ei++] = -0.1;
      positions[ei++] = nb.x || 0; positions[ei++] = nb.y || 0; positions[ei++] = -0.1;
      // colour-coded by relationship type ("that would make a ton of sense" — no link-type
      // palette exists server-side, so a stable hash-to-hue keeps a given edge type the
      // same colour across reloads without inventing new server state).
      ec.set(colorForEdgeType(e.type));
      edgeColors[eci++] = ec.r; edgeColors[eci++] = ec.g; edgeColors[eci++] = ec.b;
      edgeColors[eci++] = ec.r; edgeColors[eci++] = ec.g; edgeColors[eci++] = ec.b;
    }
    const edgeGeo = new THREE.BufferGeometry();
    edgeGeo.setAttribute("position", new THREE.BufferAttribute(positions.subarray(0, ei), 3));
    edgeGeo.setAttribute("color", new THREE.BufferAttribute(edgeColors.subarray(0, ei), 3));
    const edgeMat = new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.4 });
    edgeLines = new THREE.LineSegments(edgeGeo, edgeMat);
    scene.add(edgeLines);
    applyDim();
    updateEdgeStyle();
  }

  // edges thin and fade with zoom-out — legible up close, never a solid mesh of lines once
  // you're far enough out to see everything at once.
  function updateEdgeStyle() {
    if (!edgeLines) return;
    const t = Math.min(1, Math.max(0, (viewSize - 100) / 1200)); // 0 near, 1 far
    // base edges stay quiet at every zoom now that a focus gets its own brighter overlay
    // (updateHighlightEdges) — this layer is texture/context, never the signal.
    edgeLines.material.opacity = 0.28 - t * 0.22;
  }

  // click = HIGHLIGHT, never a data change: dim everything except the focused node + its
  // lit neighborhood (empty litIds = nothing dimmed, the normal unfocused view).
  function applyDim() {
    if (!mesh) return;
    const color = new THREE.Color();
    const dimmed = litIds.size > 0 || focusId;
    for (let i = 0; i < idToNode.length; i++) {
      const nd = idToNode[i];
      color.set(typeColors.get(nd.type) || "#6e7681");
      if (dimmed && nd.id !== focusId && !litIds.has(nd.id)) {
        color.multiplyScalar(0.12); // dim hard — colour as signal, not decoration
      } else if (nd.id === focusId) {
        color.set("#58a6ff");
      }
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
    }
    mesh.instanceColor.needsUpdate = true;
  }

  async function loadTypeColors() {
    const cat = await fetch("/schema").then((r) => r.json());
    const m = new Map();
    for (const t of cat.object_types) m.set(t.name, t.color || "#6e7681");
    return m;
  }

  // frames the camera around the REAL bounding box of whatever's currently loaded —
  // the fixed viewSize=1300 default this used to reset to was measured against the old
  // force-relax layout's small extent and reads as "zoomed into one dense cluster" against
  // Khnum's new deterministic hash-placement layout, whose extent can run tens of
  // thousands of world units wide depending on how far apart two project hashes land.
  function fitToNodes(list) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const nd of list) {
      if (nd.x == null || nd.y == null) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    if (!Number.isFinite(minX)) return;
    camera.position.x = (minX + maxX) / 2;
    camera.position.y = (minY + maxY) / 2;
    const span = Math.max(maxX - minX, maxY - minY, 0);
    viewSize = Math.max(30, span * 1.1 + 40);
    updateFrustum();
    rescaleForZoom();
  }

  setStatus("loading the whole graph…");
  let { nodes, edges } = await fetchStreamSnapshot();
  buildScene(nodes, edges);
  fitToNodes(nodes);
  setStatus(`${nodes.length} objects, ${edges.length} edges`);
  levelBadge.textContent = "whole graph";

  // ---- deltas: GET /graph/stream/deltas is an SSE poll-diff over the outbox, keyed by
  // object id (not array index — see the module docstring). Applied live so the canvas
  // never needs a full reload after the first snapshot; a 'retired' delta drops the node
  // from the next full rebuild rather than trying to hide a single InstancedMesh instance
  // (there is no per-instance visibility toggle cheaper than a rebuild at this node count).
  let nodesById = new Map(nodes.map((nd) => [nd.id, nd]));
  let pendingRebuild = false;
  function scheduleRebuild() {
    if (pendingRebuild) return;
    pendingRebuild = true;
    setTimeout(() => {
      pendingRebuild = false;
      nodes = Array.from(nodesById.values());
      buildScene(nodes, edges);
      if (focusId) applyDim();
      setStatus(`${nodes.length} objects, ${edges.length} edges (live)`);
    }, 250);
  }
  try {
    const es = new EventSource("/graph/stream/deltas");
    es.onmessage = (ev) => {
      let delta;
      try { delta = JSON.parse(ev.data); } catch { return; }
      if (delta.op === "retired") {
        nodesById.delete(delta.id);
        scheduleRebuild();
      } else if (delta.op === "moved") {
        const nd = nodesById.get(delta.id);
        if (nd && delta.x != null && delta.y != null) { nd.x = delta.x; nd.y = delta.y; scheduleRebuild(); }
      }
    };
    es.onerror = () => { /* browser auto-reconnects an EventSource; nothing to do here */ };
  } catch (err) {
    console.error("graph/stream/deltas unavailable", err);
  }

  // "highlight nodes all the way upstream" — walked CLIENT-SIDE off the already-loaded
  // edge list (the whole-graph load makes this free: no new endpoint). Osiris convention
  // is from_id = the dependent/newer fact, to_id = what it points at (grounds/cites/
  // spawned_by/etc all read this way) — so upstream from X follows OUTGOING edges
  // (X.source -> target), repeated until nothing new turns up. Capped so one hyper-
  // connected node can't pull in a meaningful fraction of the whole graph.
  const outAdj = new Map(); // node id -> [target ids]
  for (const e of edges) {
    if (!outAdj.has(e.source)) outAdj.set(e.source, []);
    outAdj.get(e.source).push(e.target);
  }
  const UPSTREAM_CAP = 400;
  function walkUpstream(startId) {
    const seen = new Set([startId]);
    const frontier = [startId];
    while (frontier.length && seen.size < UPSTREAM_CAP) {
      const cur = frontier.shift();
      for (const t of outAdj.get(cur) || []) {
        if (seen.has(t)) continue;
        seen.add(t);
        frontier.push(t);
        if (seen.size >= UPSTREAM_CAP) break;
      }
    }
    return seen;
  }

  // a second, brighter LineSegments drawn OVER the dim base edges — only edges strictly
  // between two currently-lit nodes, so the focus's own provenance chain visually pops
  // instead of reading as the same grey wash as everything else.
  let highlightEdges = null;
  function updateHighlightEdges() {
    if (highlightEdges) { scene.remove(highlightEdges); highlightEdges.geometry.dispose(); highlightEdges.material.dispose(); highlightEdges = null; }
    if (!litIds.size) return;
    const idx = new Map(idToNode.map((nd, i) => [nd.id, nd]));
    const pos = [];
    for (const e of edges) {
      if (!litIds.has(e.source) || !litIds.has(e.target)) continue;
      const a = idx.get(e.source), b = idx.get(e.target);
      if (!a || !b) continue;
      pos.push(a.x || 0, a.y || 0, -0.05, b.x || 0, b.y || 0, -0.05);
    }
    if (!pos.length) return;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(pos), 3));
    highlightEdges = new THREE.LineSegments(
      geo, new THREE.LineBasicMaterial({ color: 0x58a6ff, transparent: true, opacity: 0.8 }));
    scene.add(highlightEdges);
  }

  // ---- pan + zoom — LOOKING ONLY, never changes what's loaded ------------------------
  // a browser fires a real "click" event at pointerup even after a long drag, as long as
  // it lands back on the same element — the operator caught this exactly ("dragging and
  // releasing... refocuses on a random object"). Track total drag distance and only treat
  // the click handler's pick as genuine below a small pixel threshold; anything past that
  // was a pan, not a click, and the trailing click event is swallowed.
  let dragging = false, lastX = 0, lastY = 0, dragDistance = 0;
  const CLICK_SLOP_PX = 4;
  renderer.domElement.addEventListener("pointerdown", (ev) => {
    dragging = true; lastX = ev.clientX; lastY = ev.clientY; dragDistance = 0;
  });
  window.addEventListener("pointerup", () => { dragging = false; });
  window.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const dx = ev.clientX - lastX, dy = ev.clientY - lastY;
    lastX = ev.clientX; lastY = ev.clientY;
    dragDistance += Math.hypot(dx, dy);
    const wpx = (camera.right - camera.left) / wrap.clientWidth;
    const wpy = (camera.top - camera.bottom) / wrap.clientHeight;
    camera.position.x -= dx * wpx;
    camera.position.y += dy * wpy;
    // no scheduleLabelUpdate() here — label POSITIONS track every render frame now (see
    // the render loop below); only WHICH labels show is still debounced (scheduleLabelPick)
    scheduleLabelPick();
  });

  function rescaleForZoom() {
    if (!mesh || !idToNode.length) return;
    const wpp = worldPerPx();
    const dummy = new THREE.Object3D();
    const m = new THREE.Matrix4();
    for (let i = 0; i < idToNode.length; i++) {
      const nd = idToNode[i];
      mesh.getMatrixAt(i, m);
      m.decompose(dummy.position, dummy.quaternion, dummy.scale);
      dummy.scale.setScalar(nd.radiusPx * wpp);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      pickMesh.setMatrixAt(i, dummy.matrix);
    }
    mesh.instanceMatrix.needsUpdate = true;
    pickMesh.instanceMatrix.needsUpdate = true;
    updateEdgeStyle();
  }

  renderer.domElement.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      viewSize = Math.max(8, Math.min(2000, viewSize * Math.exp(ev.deltaY * 0.001)));
      updateFrustum();
      rescaleForZoom();
      scheduleLabelPick();
    },
    { passive: false }
  );

  // ---- GPU picking (unchanged from the spike) ----------------------------------------
  const pickTarget = new THREE.WebGLRenderTarget(1, 1);
  const pickBuf = new Uint8Array(4);
  function pickAt(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    // setViewOffset's (x,y) origin is TOP-LEFT (matching a mouse event's own coordinates,
    // per its own docstring's tile-grid example: A/B/C at y=0 sit ABOVE D/E/F at y=h) —
    // NOT WebGL's bottom-left convention. The earlier flip here was the real cause of
    // clicks missing the node visually under the cursor.
    const px = (clientX - rect.left) * (window.devicePixelRatio || 1);
    const py = (clientY - rect.top) * (window.devicePixelRatio || 1);
    camera.setViewOffset(renderer.domElement.width, renderer.domElement.height, px, py, 1, 1);
    renderer.setRenderTarget(pickTarget);
    renderer.render(pickScene, camera);
    renderer.setRenderTarget(null);
    camera.clearViewOffset();
    renderer.readRenderTargetPixels(pickTarget, 0, 0, 1, 1, pickBuf);
    const id = pickBuf[0] | (pickBuf[1] << 8) | (pickBuf[2] << 16);
    return id === 0 ? null : idToNode[id - 1];
  }

  renderer.domElement.addEventListener("click", (ev) => {
    if (dragDistance > CLICK_SLOP_PX) return; // the trailing click after a real pan/drag
    const hit = pickAt(ev.clientX, ev.clientY);
    if (hit) focusObject(hit.id);
    else clearFocus();
  });

  function clearFocus() {
    focusId = null; litIds = new Set();
    applyDim();
    updateHighlightEdges();
    rightRail.className = "rail";
    rightRail.innerHTML =
      '<div class="insp-empty" id="insp"><div style="font-weight:700;font-size:13px;' +
      'letter-spacing:0.5px;text-transform:uppercase;color:var(--text);margin-bottom:8px">' +
      "Provenance Inspector</div>Click any object to inspect its evidence grade and relationships.</div>";
    setStatus(`${idToNode.length} objects, ${edges.length} edges`);
    if (onFocus) onFocus(null); // shares the clear with an embedding table (console.js)
  }

  fitBtn.addEventListener("click", () => {
    fitToNodes(idToNode);
    scheduleLabelPick();
  });
  upBtn.addEventListener("click", clearFocus);

  // ---- focus = HIGHLIGHT, never a reload (inspector stays HTML) ---------------------
  async function focusObject(id) {
    focusId = id;
    litIds = walkUpstream(id);
    if (onFocus) onFocus(id); // shares the selection with an embedding table (console.js)

    // zoom-to-fit: frame the camera around exactly the lit set's own bounding box (padded),
    // not a fixed small viewSize centered on the click — "zooming them to where they make
    // sense," per the operator. A single-node upstream (nothing else lit) still gets a
    // sane close-in view rather than a zero-size frustum.
    const idx = new Map(idToNode.map((nd) => [nd.id, nd]));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const litId of litIds) {
      const nd = idx.get(litId);
      if (!nd || nd.x == null || nd.y == null) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    if (Number.isFinite(minX)) {
      camera.position.x = (minX + maxX) / 2;
      camera.position.y = (minY + maxY) / 2;
      const span = Math.max(maxX - minX, maxY - minY, 0);
      viewSize = Math.max(30, Math.min(1300, span * 1.6 + 40)); // padding + a sane floor/ceiling
      updateFrustum();
      rescaleForZoom();
    }

    applyDim();
    updateHighlightEdges();
    setStatus(`focused: ${litIds.size} upstream (capped at ${UPSTREAM_CAP})`);
    scheduleLabelPick();
    await inspect(id);
  }

  async function inspect(id) {
    const obj = await fetch(`/objects/${id}`).then((r) => r.json());
    rightRail.className = "rail";
    rightRail.innerHTML = Osiris.objectDetail(obj, "");
    const relsEl = rightRail.querySelector("[data-rels]");
    if (relsEl) await Osiris.loadRels(relsEl, id, (pickId) => focusObject(pickId), () => {});
  }

  // ---- search (stays HTML) -----------------------------------------------------------
  let searchTimer = null, searchToken = 0;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchInput.value.trim();
    if (!q) { searchDd.style.display = "none"; return; }
    const myToken = ++searchToken;
    searchTimer = setTimeout(async () => {
      const res = await fetch(`/search?q=${encodeURIComponent(q)}&limit=8`).then((r) => r.json());
      if (myToken !== searchToken) return;
      const hits = Array.isArray(res.hits) ? res.hits : [];
      searchDd.innerHTML = "";
      searchDd.style.display = hits.length ? "block" : "none";
      for (const h of hits) {
        const row = document.createElement("div");
        row.className = "dd-item";
        row.textContent = h.label || h.id;
        row.addEventListener("click", async () => {
          searchDd.style.display = "none";
          searchInput.value = "";
          await focusObject(h.id); // zoom-to-fit reads the lit set's own bbox, no x/y needed here
        });
        searchDd.appendChild(row);
      }
    }, 200);
  });

  // ---- labels: WHICH ones (debounced, expensive nearest-N) vs WHERE they sit (every
  // frame, cheap) — the split that fixes the lag the operator caught. ------------------
  // "show node ids, agent names, project names... things that are short, let expansion
  // happen in the inspector" — resolve_label() already returns a short display name for
  // most types (Agent/SoftwareProject/etc), but for Decision/Thread it falls back to the
  // FULL summary text, which is exactly the "wall of garbage text" in the screenshot.
  // Never truncate-with-ellipsis (a chopped sentence still reads as garbage) — fall back
  // to a clean type + short id instead, the same shape a real "node id" reads as.
  const SHORT_LABEL_MAX = 26;
  function shortLabel(nd) {
    const label = nd.label || "";
    if (label && label.length <= SHORT_LABEL_MAX) return label;
    return `${nd.type} ${nd.id.slice(0, 8)}`;
  }

  const N_LABELS = 40;
  let labeledNodes = [];
  const labelDivs = new Map(); // node -> div, reused across frames instead of rebuilt
  let labelPickTimer = null;
  function scheduleLabelPick() {
    if (labelPickTimer) return;
    labelPickTimer = setTimeout(() => { labelPickTimer = null; pickLabels(); }, 150);
  }
  function pickLabels() {
    const cx = camera.position.x, cy = camera.position.y;
    const n = Math.max(10, Math.round(N_LABELS - (viewSize / 2000) * 30));
    const pool = litIds.size ? idToNode.filter((nd) => nd.id === focusId || litIds.has(nd.id)) : idToNode;
    labeledNodes = pool
      .map((nd) => ({ nd, d: (nd.x - cx) ** 2 + (nd.y - cy) ** 2 }))
      .sort((a, b) => a.d - b.d)
      .slice(0, n)
      .map((e) => e.nd);
    // reconcile DOM: remove divs for nodes no longer labeled, add for newly labeled ones —
    // reuses existing elements instead of an innerHTML rebuild every pick.
    const wanted = new Set(labeledNodes);
    for (const [nd, div] of labelDivs) {
      if (!wanted.has(nd)) { div.remove(); labelDivs.delete(nd); }
    }
    for (const nd of labeledNodes) {
      if (labelDivs.has(nd)) continue;
      const div = document.createElement("div");
      div.className = "lbl";
      div.textContent = shortLabel(nd);
      labelsEl.appendChild(div);
      labelDivs.set(nd, div);
    }
  }
  // runs every render frame — cheap (one project() + style write per already-chosen label,
  // no sort, no DOM create/destroy) so labels track the scene with zero perceptible lag.
  // DECLUTTER: "present text without it looking like garbage" — labeledNodes is already
  // nearest-to-camera-first (from pickLabels' own sort), so a plain greedy pass — show a
  // label unless its screen box would overlap one already placed this frame — keeps the
  // closest/most-relevant labels and silently drops the rest, rather than stacking dozens
  // of overlapping strings into an unreadable wall of text.
  const _v = new THREE.Vector3();
  const _placed = []; // [x0,y0,x1,y1] boxes already shown this frame
  const LABEL_W = 90, LABEL_H = 16, LABEL_GAP = 4;
  function overlapsPlaced(x, y) {
    const x0 = x - LABEL_W / 2, x1 = x + LABEL_W / 2, y0 = y - LABEL_H, y1 = y;
    for (const b of _placed) {
      if (x0 < b[2] + LABEL_GAP && x1 > b[0] - LABEL_GAP && y0 < b[3] + LABEL_GAP && y1 > b[1] - LABEL_GAP) return true;
    }
    return false;
  }
  function positionLabels() {
    _placed.length = 0;
    for (const nd of labeledNodes) {
      const div = labelDivs.get(nd);
      if (!div) continue;
      _v.set(nd.x || 0, nd.y || 0, 0).project(camera);
      const x = (_v.x * 0.5 + 0.5) * wrap.clientWidth;
      const y = (-_v.y * 0.5 + 0.5) * wrap.clientHeight;
      const lit = nd.id === focusId || litIds.has(nd.id);
      // lit/focused labels always win their spot (never declutter the thing you asked to
      // see); ordinary labels yield to anything already placed.
      if (!lit && overlapsPlaced(x, y)) { div.hidden = true; continue; }
      div.hidden = false;
      div.style.left = `${x}px`;
      div.style.top = `${y}px`;
      div.className = "lbl" + (lit ? " lit" : "");
      _placed.push([x - LABEL_W / 2, y - LABEL_H, x + LABEL_W / 2, y]);
    }
  }

  // ---- render loop ---------------------------------------------------------------------
  function schedule(cb) {
    let done = false;
    const rafId = requestAnimationFrame(() => { if (!done) { done = true; cb(); } });
    const toId = setTimeout(() => { if (!done) { done = true; cancelAnimationFrame(rafId); cb(); } }, 50);
    return () => { cancelAnimationFrame(rafId); clearTimeout(toId); };
  }
  function loop() {
    renderer.render(scene, camera);
    positionLabels();
    schedule(loop);
  }
  loop();
  pickLabels();

  const api = {
    focusObject, clearFocus, inspect,
    get idToNode() { return idToNode; },
    camera, pickAt, mesh: () => mesh, worldPerPx, nodeRadiusPx, renderer,
  };
  window.__space = api; // kept for existing debugging/test scripts, same shape as before
  return api;
}
