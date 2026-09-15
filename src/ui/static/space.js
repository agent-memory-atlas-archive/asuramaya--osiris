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

// THE READING LAYER, part A: EDGE CLASSES (ruling c5953bb1, Thoth DM 10596). Structural
// edges are pure containment/membership (an object belongs to a repo, an agent operates in
// a project, a seat holds a mind) — real, but not what a reader is tracing when they ask
// "how did we get here"; their degree dwarfs everything else (repo:osiris alone: 20,352).
// Semantic edges are the actual provenance/evidence trail (possible_upstream, cites,
// derived_from, spawned_by, succeeded_from, supersedes, resolves, grounded_by, and the
// rest) — what "focus really focusing" (the operator's own words) needs to walk and show.
//
// DEFAULT, picked and noted here per Thoth's own instruction not to park on visual choices
// (thread 71c4ca0d carries this note too): every link type in src/ontology/schema.py whose
// own docstring reads as "X belongs to / operates in / is a member or officer of Y" is
// structural; everything else defaults to semantic (the safer default — an edge that's
// actually structural but misclassified just draws a bit more clutter; one that's actually
// meaningful but misclassified as structural would go invisible, the worse failure).
// Swaps to Khnum's real per-request `edge_classes` header field the moment it lands (DM
// 10603), same fallback pattern as colorForEdgeType/edge_types before it.
const STRUCTURAL_EDGE_TYPES = new Set([
  "in_repo", "works_in", "governs", "holds", "acts_for", "member_of", "employs",
  "worktree_of", "succeeds_seat", "owns", "owned_by", "subsidiary_of", "ultimate_parent",
  "sent_by", "addressed_to", "broadcast_to", "in_thread",
]);
function classOfEdgeType(type) {
  return STRUCTURAL_EDGE_TYPES.has(type) ? "structural" : "semantic";
}

// THE READING LAYER, part B (ruling c5953bb1): the curated provenance/evidence edge-type
// allowlist a real FOCUS walks — the actual "long paths leading back and upstream" the
// operator asked to see, as opposed to part A's structural containment edges, which never
// widen a path. Pure, DOM-free module-level functions (not closures inside initSpace) so
// the acceptance test Thoth's own dispatch named — "a synthetic 5-hop chain where focus at
// the tail lights exactly the chain and nothing else" — can exercise the real algorithm
// directly via Node, not a string-presence proof.
// THE LEGIBILITY PASS, TIP 1 AMENDMENT (operator via Thoth mail 10726, ruling amending
// e1cb9e3b): "the lens is the TREE TO SOURCE" -- grounded_by, decided_in, answers added to
// the walk so a decision's own grounding trail is reachable, not just its narrower
// derivation chain.
export const PATH_EDGE_TYPES = new Set([
  "possible_upstream", "cites", "derived_from", "spawned_by",
  "succeeded_from", "supersedes", "resolves", "grounded_by", "decided_in", "answers",
]);
export function buildPathAdjacency(edges) {
  const outAdj = new Map(), inAdj = new Map(); // node id -> [neighbor ids]
  for (const e of edges) {
    if (!PATH_EDGE_TYPES.has(e.type)) continue;
    if (!outAdj.has(e.source)) outAdj.set(e.source, []);
    outAdj.get(e.source).push(e.target);
    if (!inAdj.has(e.target)) inAdj.set(e.target, []);
    inAdj.get(e.target).push(e.source);
  }
  return { outAdj, inAdj };
}
// bidirectional BFS, depth-limited (a widen control raises depth interactively rather than
// a hardcoded ceiling) — Osiris convention: from_id = the dependent/newer fact, to_id =
// what it points at, so "upstream" follows outAdj (X.source -> target) and "downstream...
// over the same reversed" follows inAdj.
export function walkPath(outAdj, inAdj, startId, depth) {
  const seen = new Set([startId]);
  let frontier = [startId];
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = [];
    for (const cur of frontier) {
      for (const t of outAdj.get(cur) || []) { if (!seen.has(t)) { seen.add(t); next.push(t); } }
      for (const t of inAdj.get(cur) || []) { if (!seen.has(t)) { seen.add(t); next.push(t); } }
    }
    frontier = next;
  }
  return seen;
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
      // TIP 1b (Thoth mail 10755): "swap the client label fallback for the header
      // labels" -- Khnum's own labels array (index-aligned to object_ids, tip 2g) is
      // the label now, computed server-side with the exact same per-type rule and
      // 40-char truncation THE LEGIBILITY PASS specified. Falls back to a client-built
      // string only if an older snapshot lacks the field.
      label: snap.labels ? snap.labels[i] : undefined,
    });
  }
  const edges = [];
  for (let i = 0; i < snap.edge_count; i++) {
    const type = (snap.edge_types && snap.edge_types[snap.edge_type_code[i]]) ?? snap.edge_type_code[i];
    // prefers Khnum's own real per-type classification (DM 10603) once the header carries
    // one; falls back to the client-side default (classOfEdgeType) until then.
    const edgeClass = snap.edge_classes && snap.edge_classes[snap.edge_type_code[i]]
      ? snap.edge_classes[snap.edge_type_code[i]]
      : classOfEdgeType(type);
    edges.push({
      source: snap.object_ids[snap.edge_src[i]],
      target: snap.object_ids[snap.edge_dst[i]],
      type, edgeClass,
    });
  }
  // THE LAST RENDERER (operator ruling d7d55257, Thoth mail 11066) killed LOD entirely --
  // Khnum's own project_aggregates/type_aggregates/cluster_edges/type_pair_edges (tip
  // 2i/2j/h) still ride the same wire header, but nothing client-side reads them any more;
  // node.type/node.project are already resolved to name strings above, off the same
  // types/projects tables those aggregates were keyed against.
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
    rightRail: (container && container.rightRail) || byId("right"),
    fitBtn: (container && container.fitBtn) || byId("fit-btn"),
    upBtn: (container && container.upBtn) || byId("up-btn"),
    legendBtn: (container && container.legendBtn) || byId("legend-btn"),
    legendPanel: (container && container.legendPanel) || byId("legend-panel"),
    backBtn: (container && container.backBtn) || byId("back-btn"),
    // TIP 1 AMENDMENT: "Widen" is retired -- depth is unlimited by default now ("until
    // roots"), so raising a capped depth is moot. The same button/id is repurposed as the
    // downstream toggle ("downstream is a toggle, off by default").
    downstreamBtn: (container && container.downstreamBtn) || byId("downstream-btn"),
    onFocus: (container && container.onFocus) || null, // (id) => void, shares selection with the table
  };
}

export async function initSpace(container) {
  const { wrap, labelsEl, statusEl, levelBadge, rightRail, fitBtn, upBtn,
    legendBtn, legendPanel, backBtn, downstreamBtn, onFocus } =
    resolveContainer(container);
  function setStatus(text) { statusEl.textContent = text; }

  const typeColors = await loadTypeColors();

  // ---- renderer / scene / camera -----------------------------------------------------
  // pixel ratio capped at 1.5 and antialias only below/at native DPR (Thoth's own live
  // measurement on an Iris Xe box, mail 10581): AA is a real GPU cost that scales with
  // resolution, and stacking it on top of an already-high device pixel ratio was part of
  // what made a real laptop GPU choke on this scene.
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const renderer = new THREE.WebGLRenderer({ antialias: dpr <= 1 });
  // three.js's ColorManagement converts every hex colour (THREE.Color.set('#8ab4f8')) from
  // sRGB into LINEAR space internally — without this, the renderer displays those linear
  // values as-is, reading systematically darker than the real colour.
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setPixelRatio(dpr);
  renderer.setSize(wrap.clientWidth, wrap.clientHeight);
  wrap.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0d1219);
  const pickScene = new THREE.Scene();

  // THE LAST RENDERER (operator ruling d7d55257, Thoth mail 11066): "a per-pixel saturation
  // cap (tone-map the additive pass...) so overlap reads as brightness and never as white."
  // Additive blending alone can sum well past 1.0 per channel and hard-clip to flat white
  // the moment enough points/edges overlap the same pixel -- a genuine HDR render target
  // (HalfFloatType, values free to exceed 1.0) plus a Reinhard tone-map full-screen pass
  // (color / (color + 1), mathematically bounded in [0, 1) for any non-negative input, no
  // matter how many instances overlap) makes "never white" a property of the math, not a
  // heuristic. Falls back to rendering straight to the canvas if the render target can't be
  // created (an old GPU lacking float render-target support) -- the additive brightness cap
  // this buys is a real improvement, never a hard requirement to render at all.
  let sceneTarget = null, toneMapScene = null, toneMapCamera = null;
  try {
    sceneTarget = new THREE.WebGLRenderTarget(1, 1, {
      type: THREE.HalfFloatType, depthBuffer: true, stencilBuffer: false,
    });
    toneMapCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    toneMapScene = new THREE.Scene();
    const toneMapMaterial = new THREE.ShaderMaterial({
      uniforms: { tDiffuse: { value: sceneTarget.texture } },
      depthTest: false, depthWrite: false,
      vertexShader: `
        varying vec2 vUv;
        void main() { vUv = uv; gl_Position = vec4(position, 1.0); }
      `,
      fragmentShader: `
        uniform sampler2D tDiffuse;
        varying vec2 vUv;
        void main() {
          vec3 color = texture2D(tDiffuse, vUv).rgb;
          vec3 mapped = color / (color + vec3(1.0)); // Reinhard: bounded in [0,1) always
          gl_FragColor = vec4(mapped, 1.0);
        }
      `,
    });
    toneMapScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), toneMapMaterial));
  } catch (err) {
    console.error("tone-map render target unavailable, rendering without a saturation cap", err);
    sceneTarget = null;
  }
  function resizeSceneTarget() {
    if (!sceneTarget) return;
    sceneTarget.setSize(
      Math.max(1, Math.round(wrap.clientWidth * dpr)),
      Math.max(1, Math.round(wrap.clientHeight * dpr)));
  }

  // world extent depends entirely on Khnum's own layout heartbeat (deterministic hash
  // placement, piece A) and is NOT a fixed constant — a project-center hash can land
  // anywhere; fitToNodes() (below) frames the camera from the real loaded bbox instead of
  // a guessed number the moment the first snapshot lands, and Fit re-measures live rather
  // than resetting to a stale guess. minViewSize/maxViewSize (Thoth's own live-verified fix,
  // mail 10581) are likewise derived from the real fitted bbox, not the old hardcoded
  // [8, 2000] clamp — that clamp predated the deterministic layout and let one wheel tick
  // snap a 259,779-unit-wide view down to 2,862 (a 90x jump into a single dense cluster,
  // read by the operator as "zoom does not work").
  let viewSize = 1300;
  let minViewSize = 20, maxViewSize = 2000;
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
  resizeSceneTarget();
  window.addEventListener("resize", () => {
    renderer.setSize(wrap.clientWidth, wrap.clientHeight);
    updateFrustum();
    resizeSceneTarget();
    edgeFadeUniforms.uViewportPx.value.set(wrap.clientWidth, wrap.clientHeight);
    markDirty();
  });

  // ---- render ON DEMAND (Thoth's own live measurement, mail 10581): the render loop used
  // to run every frame forever (rAF plus a 50ms setTimeout fallback), even when browse
  // isn't the active surface or the tab is hidden — pure waste, and on top of the
  // per-wheel-event instance-buffer rewrite this fix removes below, it compounded into the
  // "super fried" report. Now a frame only renders when something actually changed
  // (camera move, data, focus, label pick); the loop stops scheduling itself entirely once
  // idle rather than polling at 20fps forever.
  let dirty = true, running = true, rafPending = false;
  function markDirty() {
    dirty = true;
    if (!running || rafPending) return;
    rafPending = true;
    requestAnimationFrame(renderIfDirty);
  }
  // shared by the render-on-demand loop and the api's own forceRender debug hook -- one
  // place decides whether the tone-map pass runs, never duplicated.
  function renderScene() {
    if (sceneTarget) {
      renderer.setRenderTarget(sceneTarget);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);
      renderer.render(toneMapScene, toneMapCamera);
    } else {
      renderer.render(scene, camera);
    }
  }
  function renderIfDirty() {
    rafPending = false;
    if (!running || !dirty) return;
    renderScene();
    positionLabels();
    dirty = false;
  }
  function pause() { running = false; }
  function resume() { running = true; markDirty(); }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause(); else resume();
  });

  // WebGL context loss (Thoth's own live report: the first load in her tab was refused
  // outright, "Web page caused context loss and was blocked", and a later tab vanished —
  // a GPU reset Chrome then blocks the page from reusing). preventDefault on loss keeps the
  // browser from tearing the canvas down permanently; rebuild GPU resources on restore
  // instead of leaving a dead black canvas or crashing the tab.
  renderer.domElement.addEventListener("webglcontextlost", (ev) => {
    ev.preventDefault();
    pause();
    setStatus("WebGL context lost — recovering…");
  }, false);
  renderer.domElement.addEventListener("webglcontextrestored", () => {
    setStatus("WebGL context restored — rebuilding…");
    if (idToNode.length) {
      buildScene(idToNode, edges);
      fitToNodes(idToNode);
      setStatus(`${idToNode.length} objects, ${edges.length} edges (recovered)`);
    }
    resume();
  }, false);

  let mesh = null, pickMesh = null, edgeLines = null;
  let meshUniforms = null, pickUniforms = null;
  let visibleAttr = null;
  let idToNode = [];
  // one id->node index, rebuilt only when the node set itself changes (buildScene) --
  // TIP 1 AMENDMENT's own 100ms budget (mail 10726 item 2) made this the fix, not a
  // premature one: rebuilding a 49k-entry Map costs ~15ms each, and focusObject used to
  // build FOUR of them (ego layout, restore, path edges, camera fit) on every single click.
  let idById = new Map();
  // TIP 1(e): header taxonomy-pill type filters hide instances through the same per-instance
  // aVisible flag focus uses (1(d)) — empty means nothing filtered, everything shown.
  let hiddenNodeTypes = new Set();
  // CONSOLE CHROME CLEANUP piece 2 (decision 31717ca7, thread 0be2f790's own operator-
  // finding follow-up): the header's repo selector drives the SAME aVisible flag through
  // this sibling set — nd.project (already carried on every node since the snapshot's own
  // project_code lookup, line ~148) is the field it filters on, empty means nothing
  // filtered, everything shown.
  let hiddenProjects = new Set();
  // THE DRILL, item 5 (Thoth mail 11048): ids a project-filter stub click revealed --
  // "without unhiding the project" itself, so this stays a NARROW override, never merged
  // into hiddenProjects. Reset whenever the filter itself changes (setHiddenProjects).
  let revealedStubIds = new Set();
  // THE READING LAYER, part B: FOCUS = PATH LENS (ruling c5953bb1, Thoth DM 10596). SELECT
  // (a plain click) and FOCUS (double-click, Enter, or the inspector's Focus button) are now
  // two different acts — selectedId just shows the inspector; pathFocusId/pathReachable are
  // the real path-lens state (only non-empty while an actual focus is active).
  let selectedId = null;
  let pathFocusId = null;
  let pathReachable = new Set();
  let focusStack = []; // ids, most recent last — back() pops, Escape/Clear focus wipes the overlay
  // THE DRILL: cross-project anchor click targets. Declared here (not next to
  // buildProjectObjectIndex/buildProjectAnchors further down) because buildScene calls
  // buildProjectObjectIndex() on every load -- a `let` declared below buildScene's own
  // call site is still in its temporal dead zone at that point, a real crash-on-every-load
  // bug THE LAST RENDERER's live verification caught (ReferenceError: Cannot access
  // 'projectObjectByName' before initialization).
  let projectObjectByName = new Map(); // "repo:foo" -> that SoftwareProject object's own id
  // shared world->screen scratch vector for every per-frame div-positioning pass (labels,
  // drill entries, project anchors, project stubs) -- same TDZ reasoning as
  // projectObjectByName above: positionDrillDivs/positionProjectAnchors/
  // positionProjectStubs are all reachable during a container focus called well before a
  // declaration placed down near positionLabels would have run. One shared instance is
  // also just correct: it's pure per-call scratch, never carries state between calls.
  const _screenV = new THREE.Vector3();

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

  // THE LAST RENDERER (operator ruling d7d55257, Thoth mail 11066, freezing the renderer):
  // "points at a constant SCREEN size in px on a steep degree curve (~2px leaf, ~8px@100
  // links, ~16px@1000, ~32px@10000; no world-unit sizing, no 48px cap)." A full circle back
  // to this file's own ORIGINAL pre-legibility-pass scheme (aRadiusPx * uWorldPerPx, a
  // constant screen size regardless of zoom) -- what changed since is the CURVE, not the
  // mechanism: px = 2 * degree^log10(2) hits all four of the ruling's own anchors exactly
  // (degree 1 -> 2px, 100 -> 8px, 1,000 -> 16px, 10,000 -> 32px -- verified algebraically:
  // d^log10(2) = 10^(log10(d)*log10(2)) = 2^log10(d), so at d=10^k the curve is exactly
  // 2*2^k), left uncapped past that per the ruling's own words -- no world-unit sizing, no
  // LOD tiers standing in for a floor once zoomed out (kill LOD entirely, same mail).
  const DEGREE_PX_BASE = 2;
  const DEGREE_PX_EXPONENT = Math.log10(2); // ≈0.30103
  function nodeScreenPx(nd) {
    return DEGREE_PX_BASE * Math.pow(Math.max(nd.degree || 0, 1), DEGREE_PX_EXPONENT);
  }
  function worldPerPx() { return viewSize / wrap.clientHeight; }

  // TIP 4 (operator ruling "DENSITY NOT DISCS", mail 11011): "every object draws at every
  // zoom as an additive point sprite... a project far out is a haze whose brightness is its
  // count." `additive` is true for the DRAW mesh only, never the pick mesh -- GPU picking
  // decodes an exact RGB-encoded instance id out of the render target, which additive
  // blending would corrupt the moment two picked instances' colours overlap in that tiny
  // readback; the pick mesh stays fully opaque, same as before this tip. THE LAST RENDERER
  // caps overall SATURATION with a tone-map post-process pass instead (see
  // makeToneMapPass below) rather than a per-instance opacity ceiling, so raw additive
  // brightness here can exceed 1.0 without a hard per-material alpha limiting it.
  const NODE_POINT_OPACITY = 0.85;
  function makeInstancedCircleMaterial(opts) {
    const additive = !!(opts && opts.additive);
    const uniforms = { uWorldPerPx: { value: worldPerPx() } };
    const matOpts = { vertexColors: true };
    if (additive) {
      Object.assign(matOpts, {
        transparent: true, opacity: NODE_POINT_OPACITY,
        depthWrite: false, blending: THREE.AdditiveBlending,
      });
    }
    const mat = new THREE.MeshBasicMaterial(matOpts);
    mat.onBeforeCompile = (shader) => {
      shader.uniforms.uWorldPerPx = uniforms.uWorldPerPx;
      shader.vertexShader =
        "attribute float aRadiusPx;\nattribute float aVisible;\n" +
        "uniform float uWorldPerPx;\n" + shader.vertexShader;
      shader.vertexShader = shader.vertexShader.replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\n\ttransformed *= aRadiusPx * uWorldPerPx * aVisible;"
      );
    };
    mat.customProgramCacheKey = () => "circleInstancedScreenPx";
    return { material: mat, uniforms };
  }

  // THE READING LAYER, part A: edges fade by SCREEN length, not by zoom level — a long line
  // crossing most of the view (two clusters that happen to be linked) reads as noise; a
  // short local one is the actual signal. Same GPU-uniform discipline as node sizing (mail
  // 10581): each vertex carries the OTHER endpoint's world position too (`otherPosition`),
  // so the vertex shader can project both ends to screen pixels and compute the segment's
  // own on-screen length using nothing but modelViewMatrix/projectionMatrix — already
  // updated by three.js every frame for free. No per-zoom CPU work, no material.opacity
  // scalar to keep in sync (replaces the old viewSize-based updateEdgeStyle entirely).
  // THE LAST RENDERER (Thoth mail 11066): "an edge draws only when both ends are visible,
  // no structural-hop exception, with an alpha floor (~0.06) so any drawn edge is faintly
  // visible." uMinAlpha raised from 0.04 to that floor; the density-scale multiplier TIP 4
  // added (uDensityScale) is gone -- overall saturation is now bounded by the tone-map
  // post-process pass (makeToneMapPass) instead of thinning every edge's own alpha by how
  // many are on screen.
  const edgeFadeUniforms = {
    uViewportPx: { value: new THREE.Vector2(wrap.clientWidth, wrap.clientHeight) },
    uMaxFadePx: { value: 320 },
    uMinAlpha: { value: 0.06 },
    uMaxAlpha: { value: 0.5 },
  };
  function makeEdgeFadeMaterial() {
    return new THREE.ShaderMaterial({
      uniforms: edgeFadeUniforms,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      vertexShader: `
        attribute vec3 color;
        attribute vec3 otherPosition;
        uniform vec2 uViewportPx;
        uniform float uMaxFadePx;
        uniform float uMinAlpha;
        uniform float uMaxAlpha;
        varying vec3 vColor;
        varying float vAlpha;
        void main() {
          vColor = color;
          vec4 clip = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          vec4 otherClip = projectionMatrix * modelViewMatrix * vec4(otherPosition, 1.0);
          vec2 pxA = (clip.xy / clip.w * 0.5 + 0.5) * uViewportPx;
          vec2 pxB = (otherClip.xy / otherClip.w * 0.5 + 0.5) * uViewportPx;
          float screenLen = distance(pxA, pxB);
          vAlpha = mix(uMaxAlpha, uMinAlpha, clamp(screenLen / uMaxFadePx, 0.0, 1.0));
          gl_Position = clip;
        }
      `,
      fragmentShader: `
        varying vec3 vColor;
        varying float vAlpha;
        void main() { gl_FragColor = vec4(vColor, vAlpha); }
      `,
    });
  }

  // legend state: which edge classes/types are hidden from the base render. Structural is
  // hidden by DEFAULT ("not drawn at rest", ruling c5953bb1) — the legend is how a reader
  // opts back into seeing it without needing to focus a specific node.
  const hiddenEdgeClasses = new Set(["structural"]);
  const hiddenEdgeTypes = new Set();
  // TIP 1(d): "focus HIDES unreachable nodes AND EDGES" — an edge whose endpoint is
  // currently invisible (focus-unreachable or type-filtered, same aVisible flag applyDim
  // maintains) is dropped from the base layer too, not just faded; the bright path overlay
  // (updatePathEdges) draws the reachable ones on top regardless.
  function nodeVisible(nd) {
    if (!nd) return false;
    if (hiddenNodeTypes.has(nd.type)) return false;
    // THE DRILL, item 5 (Thoth mail 11048): revealedStubIds overrides a project's own
    // hidden state for the specific nodes a stub click revealed -- "without unhiding the
    // project" itself, only this one path.
    if (hiddenProjects.size > 0 && hiddenProjects.has(nd.project) && !revealedStubIds.has(nd.id)) return false;
    if (pathFocusId && nd.id !== pathFocusId && !pathReachable.has(nd.id)) return false;
    return true;
  }
  // THE LAST RENDERER (Thoth mail 11066): "an edge draws only when both ends are visible,
  // no structural-hop exception." Every prior tier/budget/bundling mechanism (TIP 3's LOD
  // cutoff, TIP 4's density scale, THE DRILL's cross-cluster Bezier bundling) is gone --
  // one universal rule, straight lines, nodeVisible on both ends, same at every zoom.
  function buildEdgeLines(nodes, edgeList) {
    if (edgeLines) { scene.remove(edgeLines); edgeLines.geometry.dispose(); edgeLines.material.dispose(); edgeLines = null; }
    const byId = new Map(nodes.map((nd) => [nd.id, nd]));
    const visible = edgeList.filter((e) =>
      !hiddenEdgeClasses.has(e.edgeClass) && !hiddenEdgeTypes.has(e.type) &&
      nodeVisible(byId.get(e.source)) && nodeVisible(byId.get(e.target)));
    const positions = new Float32Array(visible.length * 6);
    const otherPositions = new Float32Array(visible.length * 6);
    const edgeColors = new Float32Array(visible.length * 6);
    const ec = new THREE.Color();
    let vi = 0;
    for (const e of visible) {
      const na = byId.get(e.source), nb = byId.get(e.target);
      if (!na || !nb) continue;
      // colour-coded by relationship type ("that would make a ton of sense" — no link-type
      // palette exists server-side, so a stable hash-to-hue keeps a given edge type the
      // same colour across reloads without inventing new server state).
      ec.set(colorForEdgeType(e.type));
      const ax = na.x || 0, ay = na.y || 0, bx = nb.x || 0, by = nb.y || 0;
      positions[vi] = ax; positions[vi + 1] = ay; positions[vi + 2] = -0.1;
      otherPositions[vi] = bx; otherPositions[vi + 1] = by; otherPositions[vi + 2] = -0.1;
      vi += 3;
      positions[vi] = bx; positions[vi + 1] = by; positions[vi + 2] = -0.1;
      otherPositions[vi] = ax; otherPositions[vi + 1] = ay; otherPositions[vi + 2] = -0.1;
      vi += 3;
      edgeColors[vi - 6] = ec.r; edgeColors[vi - 5] = ec.g; edgeColors[vi - 4] = ec.b;
      edgeColors[vi - 3] = ec.r; edgeColors[vi - 2] = ec.g; edgeColors[vi - 1] = ec.b;
    }
    const edgeGeo = new THREE.BufferGeometry();
    edgeGeo.setAttribute("position", new THREE.BufferAttribute(positions.subarray(0, vi), 3));
    edgeGeo.setAttribute("otherPosition", new THREE.BufferAttribute(otherPositions.subarray(0, vi), 3));
    edgeGeo.setAttribute("color", new THREE.BufferAttribute(edgeColors.subarray(0, vi), 3));
    edgeLines = new THREE.LineSegments(edgeGeo, makeEdgeFadeMaterial());
    scene.add(edgeLines);
    renderLegend(edgeList, nodes);
    markDirty();
  }

  // legend: lists every class + type actually present in the loaded data, checkbox per
  // row, toggling straight into hiddenEdgeClasses/hiddenEdgeTypes and rebuilding the edge
  // geometry — a legend toggle is a rare, deliberate act, never a per-frame cost. TIP 1(e):
  // node types sit alongside edge classes now, driving the same aVisible flag the header
  // taxonomy pills drive (setHiddenTypes) — either control moves the one underlying filter.
  function renderLegend(edgeList, nodeList) {
    if (!legendPanel) return;
    const classOf = new Map();
    for (const e of edgeList) classOf.set(e.type, e.edgeClass);
    const byClass = { semantic: [], structural: [] };
    for (const [type, cls] of classOf) (byClass[cls] || (byClass[cls] = [])).push(type);
    for (const k of Object.keys(byClass)) byClass[k].sort();
    const nodeTypes = [...new Set((nodeList || []).map((nd) => nd.type))].sort();

    const classRow = (cls) => {
      const checked = hiddenEdgeClasses.has(cls) ? "" : "checked";
      const count = (byClass[cls] || []).length;
      return `<label class="legend-row legend-class"><input type="checkbox" data-legend-class="${cls}" ${checked} /> <strong>${cls}</strong> <span class="o-faint">(${count})</span></label>`;
    };
    const typeRow = (type) => {
      const checked = hiddenEdgeTypes.has(type) ? "" : "checked";
      const esc = String(type).replace(/"/g, "&quot;");
      return `<label class="legend-row legend-type"><input type="checkbox" data-legend-type="${esc}" ${checked} /> <span class="legend-swatch" style="background:${colorForEdgeType(type)}"></span>${esc}</label>`;
    };
    const nodeTypeRow = (type) => {
      const checked = hiddenNodeTypes.has(type) ? "" : "checked";
      const esc = String(type).replace(/"/g, "&quot;");
      return `<label class="legend-row legend-node-type"><input type="checkbox" data-legend-node-type="${esc}" ${checked} /> <span class="legend-swatch" style="background:${typeColors.get(type) || "#6e7681"}"></span>${esc}</label>`;
    };
    legendPanel.innerHTML =
      `<div class="legend-row legend-class"><strong>node types</strong></div>` +
      nodeTypes.map(nodeTypeRow).join("") +
      classRow("semantic") + (byClass.semantic || []).map(typeRow).join("") +
      classRow("structural") + (byClass.structural || []).map(typeRow).join("");

    legendPanel.querySelectorAll("[data-legend-node-type]").forEach((el) => {
      el.addEventListener("change", () => {
        const type = el.dataset.legendNodeType;
        if (el.checked) hiddenNodeTypes.delete(type); else hiddenNodeTypes.add(type);
        applyDim();
        buildEdgeLines(idToNode, edges);
        scheduleLabelPick(); // review flaw #5
      });
    });
    legendPanel.querySelectorAll("[data-legend-class]").forEach((el) => {
      el.addEventListener("change", () => {
        const cls = el.dataset.legendClass;
        if (el.checked) hiddenEdgeClasses.delete(cls); else hiddenEdgeClasses.add(cls);
        buildEdgeLines(idToNode, edges);
      });
    });
    legendPanel.querySelectorAll("[data-legend-type]").forEach((el) => {
      el.addEventListener("change", () => {
        const type = el.dataset.legendType;
        if (el.checked) hiddenEdgeTypes.delete(type); else hiddenEdgeTypes.add(type);
        buildEdgeLines(idToNode, edges);
      });
    });
  }
  if (legendBtn && legendPanel) {
    legendBtn.addEventListener("click", () => { legendPanel.hidden = !legendPanel.hidden; });
  }

  function buildScene(nodes, edges) {
    disposeCurrent();
    idToNode = nodes;
    idById = new Map(nodes.map((nd) => [nd.id, nd]));
    buildProjectObjectIndex(); // THE DRILL: project-anchor click targets, kept current

    const n = nodes.length;
    const geo = new THREE.CircleGeometry(1, 10);
    // this three.js build's fragment shader only multiplies by vColor (and so only shows
    // instanceColor) when USE_COLOR/USE_COLOR_ALPHA is defined, which is driven by a
    // GEOMETRY-level `color` attribute, not instanceColor alone — the vertex shader
    // computes the right colour into vColor but the fragment shader silently drops it
    // without this, rendering flat black regardless of instanceColor.
    geo.setAttribute("color", new THREE.Float32BufferAttribute(
      new Float32Array(geo.attributes.position.count * 3).fill(1), 3));
    const radiusAttr = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1)), 1);
    geo.setAttribute("aRadiusPx", radiusAttr); // shared by mesh + pickMesh, same geometry instance
    visibleAttr = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1)).fill(1), 1);
    geo.setAttribute("aVisible", visibleAttr); // TIP 1(d)/(e): per-instance hide, updated in place by applyDim

    const built = makeInstancedCircleMaterial({ additive: true });
    const mat = built.material;
    meshUniforms = built.uniforms;
    mesh = new THREE.InstancedMesh(geo, mat, Math.max(n, 1));
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const builtPick = makeInstancedCircleMaterial();
    const pickMat = builtPick.material;
    pickUniforms = builtPick.uniforms;
    pickMesh = new THREE.InstancedMesh(geo, pickMat, Math.max(n, 1));
    pickMesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(Math.max(n, 1) * 3), 3);

    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    const idColor = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const nd = nodes[i];
      nd.radiusPx = nodeScreenPx(nd);
      radiusAttr.setX(i, nd.radiusPx);
      visibleAttr.setX(i, 1);
      // "sizing more intuitive where high-degree nodes stand out without obfuscating
      // smaller nodes" — a bigger circle can still sit BEHIND a smaller one drawn later
      // in the same z-plane; give every node a tiny z bias proportional to its own radius
      // so the important (bigger) ones are always nearer the camera and never occluded.
      // Scale stays 1 here deliberately — the shader (aRadiusPx * uWorldPerPx) owns sizing.
      dummy.position.set(nd.x || 0, nd.y || 0, nd.radiusPx * 0.002);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      pickMesh.setMatrixAt(i, dummy.matrix);
      color.set(typeColors.get(nd.type) || "#6e7681");
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
      const id = i + 1;
      idColor.setRGB((id & 0xff) / 255, ((id >> 8) & 0xff) / 255, ((id >> 16) & 0xff) / 255);
      pickMesh.instanceColor.setXYZ(i, idColor.r, idColor.g, idColor.b);
    }
    radiusAttr.needsUpdate = true;
    mesh.instanceMatrix.needsUpdate = true;
    pickMesh.instanceMatrix.needsUpdate = true;
    scene.add(mesh);
    pickScene.add(pickMesh);

    buildEdgeLines(nodes, edges);
    applyDim();
    markDirty();
  }

  // TIP 1(d): focus HIDES unreachable nodes outright (per-instance aVisible flag), not a
  // dim — "no dim" per Thoth's own dispatch. Reachable-but-not-focused nodes stay visible at
  // their normal type colour (still legible as part of the path); the focused node alone
  // gets the accent colour. A type hidden via the header/legend filter (hiddenNodeTypes,
  // TIP 1(e)) is invisible regardless of focus state.
  function applyDim() {
    if (!mesh) return;
    const color = new THREE.Color();
    const focused = !!pathFocusId;
    for (let i = 0; i < idToNode.length; i++) {
      const nd = idToNode[i];
      const typeHidden = hiddenNodeTypes.has(nd.type);
      const projectHidden = hiddenProjects.size > 0 && hiddenProjects.has(nd.project) &&
        !revealedStubIds.has(nd.id);
      const focusHidden = focused && nd.id !== pathFocusId && !pathReachable.has(nd.id);
      visibleAttr.setX(i, (typeHidden || projectHidden || focusHidden) ? 0 : 1);
      color.set(typeColors.get(nd.type) || "#6e7681");
      if (focused && nd.id === pathFocusId) color.set("#58a6ff");
      else if (!focused && nd.id === selectedId) color.set("#58a6ff");
      mesh.instanceColor.setXYZ(i, color.r, color.g, color.b);
    }
    mesh.instanceColor.needsUpdate = true;
    visibleAttr.needsUpdate = true;
    markDirty();
  }

  // TIP 1(e): the header taxonomy pills' own type filter (SELECTED_ENTITY_TYPES in
  // console.js) drives this — called with the full set of types that should stay HIDDEN
  // (console.js translates its own allowlist semantics before calling). The legend's own
  // node-type checkboxes (renderLegend, below) call this too, so both controls drive the
  // exact same aVisible flag rather than two independent mechanisms.
  function setHiddenTypes(types) {
    hiddenNodeTypes = new Set(types || []);
    applyDim();
    buildEdgeLines(idToNode, edges);
    scheduleLabelPick(); // review flaw #5: labels never re-picked on a filter change before
  }

  // CONSOLE CHROME CLEANUP piece 2 (decision 31717ca7): the header's repo pill's own
  // sibling to setHiddenTypes above — same "caller hands the full HIDDEN set, translated
  // from whatever allowlist/selection semantics that caller owns" convention (console.js's
  // selectRepos()/applyRepoFilter() do the SELECTED-repos-to-hidden-repos translation, same
  // shape toggleEntityType already does for types).
  function setHiddenProjects(projects) {
    hiddenProjects = new Set(projects || []);
    // THE DRILL, item 5: a fresh filter change starts from a clean reveal state -- a stub
    // reveal was scoped to the OLD filter's own boundary, not guaranteed to still make
    // sense against a new one.
    revealedStubIds = new Set();
    applyDim();
    buildEdgeLines(idToNode, edges);
    buildProjectStubs();
    // THE LAST RENDERER (Thoth mail 11066, measured leak): "project filter hides points,
    // edges and labels of hidden projects completely." applyDim/buildEdgeLines already gate
    // points/edges via nodeVisible; labels are a separate pool (pickLabels) that needs its
    // own re-pick to drop a just-hidden project's own labels immediately, not on the next
    // debounced pan/zoom.
    scheduleLabelPick();
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
    // the wheel clamp's own bounds (Thoth's fix, mail 10581) — derived from THIS fit's real
    // span, not a guess: a floor small enough to inspect one dense cluster, a ceiling about
    // 2x the whole fitted graph so "zoom out" can't run away past anything meaningful.
    minViewSize = 20;
    maxViewSize = Math.max(span * 2, 200);
    updateFrustum();
    rescaleForZoom();
    markDirty();
  }

  // ---- THE LAST RENDERER retired the whole LOD/tier/cluster/halo machinery this comment
  // block used to introduce (operator ruling d7d55257, Thoth mail 11066: "kill LOD
  // entirely -- remove zoomLOD, label tiers, cluster_edges at far, cluster rings, the halo
  // texture, per-tier alpha, and every far/mid/near branch; delete their tests"). See
  // positionLabels/pickLabels below for the one label rule that replaces it (viewport
  // top-N by degree, de-overlapped, at every zoom, no separate project-label pass).
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
      if (pathFocusId || selectedId) applyDim();
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

  // THE READING LAYER, part B, AMENDED by TIP 1's own amendment (operator via Thoth mail
  // 10726, ruling amending e1cb9e3b): "the lens is the TREE TO SOURCE" — upstream (X's
  // OUTGOING edges, X.source -> target, Osiris's own from_id->to_id convention) walks by
  // DEFAULT, until roots (no depth cap — focusDepth is Infinity now, not a fixed 4);
  // downstream (INCOMING edges) is a TOGGLE, off by default (includeDownstream). Structural
  // containment (in_repo, works_in, ...) never widens the walk itself, per part A — only the
  // "focus is never empty" one-hop fallback below reaches into it.
  // buildPathAdjacency/walkPath are pure, DOM-free, module-level functions (below the
  // module docstring) precisely so THE ACCEPTANCE TEST Thoth's own dispatch named — "a
  // synthetic 5-hop chain where focus at the tail lights exactly the chain and nothing
  // else" — can exercise the real algorithm directly via Node, not a string-presence proof.
  const FOCUS_DEPTH_DEFAULT = Infinity; // "until roots" — walkPath/bfsHops stop naturally
  let focusDepth = FOCUS_DEPTH_DEFAULT;
  let includeDownstream = false;
  const { outAdj: outAdjPath, inAdj: inAdjPath } = buildPathAdjacency(edges);

  // EGO RELAYOUT (TIP 1's own amendment, mail 10726): while a focus is on, the reachable set
  // is relaid out LOCALLY — focus at centre, ancestors ranked leftward by hop (roots
  // farthest left), siblings spread within their own rank; downstream (when toggled) ranked
  // rightward the same way. Spacing is fixed in SCREEN pixels, converted to world units at
  // the CURRENT zoom so the fan-out reads the same size regardless of viewSize. Temporary:
  // the real stored x/y (Khnum's own layout heartbeat) is saved before the first move and
  // restored by clearFocus or before laying out a new focus — never written back anywhere.
  const EGO_COL_SPACING_PX = 150;
  const EGO_ROW_SPACING_PX = 34;
  // review flaw #2: "until roots" with no cap let a real hub (repo:osiris, degree 20,560)
  // reach 20,266 nodes in 3.1s and light the whole graph -- not a lens any more. Rank-capped
  // now: the walk still goes to genuine roots for an ordinary object, but never surfaces
  // more than this many nodes for one direction, so a hub focus stays a legible tree.
  const MAX_EGO_NODES = 300;
  let egoSaved = null; // Map<id, {x,y}> of positions the active relayout overwrote
  function bfsHops(adj, startId, depth) {
    const hops = new Map([[startId, 0]]);
    let frontier = [startId];
    for (let d = 1; d <= depth && frontier.length && hops.size < MAX_EGO_NODES; d++) {
      const next = [];
      for (const cur of frontier) {
        for (const t of adj.get(cur) || []) {
          if (hops.size >= MAX_EGO_NODES) break;
          if (!hops.has(t)) { hops.set(t, d); next.push(t); }
        }
      }
      frontier = next;
    }
    return hops;
  }
  function restoreEgoLayout() {
    if (!egoSaved) return;
    for (const [id, pos] of egoSaved) {
      const nd = idById.get(id);
      if (nd) { nd.x = pos.x; nd.y = pos.y; }
    }
    egoSaved = null;
  }
  function applyEgoLayout(focusId, hopsUp, hopsDown) {
    restoreEgoLayout(); // a fresh focus always starts from the real stored positions
    const idx = idById;
    const focusNode = idx.get(focusId);
    if (!focusNode) return;
    const cx = focusNode.x || 0, cy = focusNode.y || 0;
    // review flaw #6 (TIP 1c, Thoth mail 10891): using the CURRENT (pre-focus) worldPerPx
    // made the ego layout's own scale track whatever zoom the camera happened to be at --
    // a small reachable set following another tight focus could spiral the fit down to a
    // near-empty viewSize, where the 48px screen CAP then dominates the whole frame.
    // maxViewSize (the whole graph's own fitted scale, stable since fitToNodes) gives a
    // reference wpp that never shrinks just because the camera was already zoomed in.
    const wpp = maxViewSize / wrap.clientHeight;
    const colW = EGO_COL_SPACING_PX * wpp, rowH = EGO_ROW_SPACING_PX * wpp;
    egoSaved = new Map();
    const byRank = new Map(); // signed hop (-left/+right) -> [ids]
    for (const [id, hop] of hopsUp) {
      if (id === focusId || hop === 0) continue;
      (byRank.get(-hop) || (byRank.set(-hop, []), byRank.get(-hop))).push(id);
    }
    for (const [id, hop] of hopsDown) {
      if (id === focusId || hop === 0) continue;
      (byRank.get(hop) || (byRank.set(hop, []), byRank.get(hop))).push(id);
    }
    const seed = new Map([[focusId, { x: cx, y: cy }]]);
    for (const [signedHop, ids] of byRank) {
      const x = cx + signedHop * colW;
      ids.sort(); // deterministic, not otherwise meaningful
      ids.forEach((id, i) => {
        const nd = idx.get(id);
        if (!nd) return;
        egoSaved.set(id, { x: nd.x, y: nd.y });
        seed.set(id, { x, y: cy + (i - (ids.length - 1) / 2) * rowH });
      });
    }
    // THE DRILL, item 6: relax the rank layout's own output -- a wide rank (many siblings
    // at the same hop) used to stack in one straight column, reading as a solid bar/disc
    // once density-not-discs made every one of them a real point; the physics step spreads
    // them apart while real edges among the reachable set still pull related nodes toward
    // each other.
    const springs = [];
    for (const e of edges) {
      if (seed.has(e.source) && seed.has(e.target)) springs.push([e.source, e.target]);
    }
    const relaxed = relaxPositions(seed, springs, focusId);
    for (const [id, p] of relaxed) {
      if (id === focusId) continue;
      const nd = idx.get(id);
      if (nd) { nd.x = p.x; nd.y = p.y; }
    }
  }
  // THE DRILL (ruling d7d55257, Thoth mail 11048, item 6): "physics on the visible set
  // only: a small force step (repulsion + springs, a few hundred iterations, seeded) over
  // the expanded nodes, nothing else moves." `seed` is a Map<id,{x,y}> of STARTING
  // positions (the rank layout's own output, or a simple radial scatter for the drill) --
  // a good seed matters far more than iteration count for this to converge quickly and
  // legibly; `edges` is a list of [aId, bId] pairs to spring together (real reachable-set
  // edges for an ego tree, synthetic hub-to-child pairs for a drill's star topology).
  // `fixedId` (usually the focus/hub) never moves. O(n^2) repulsion is fine at this scale
  // -- the whole point of THE DRILL and the ego cap is that n never exceeds MAX_EGO_NODES.
  const EGO_FORCE_ITERATIONS = 180;
  const EGO_REPULSION = 3200;
  const EGO_SPRING = 0.02;
  function relaxPositions(seed, springs, fixedId) {
    const ids = [...seed.keys()];
    if (ids.length < 2) return seed;
    const pos = new Map(ids.map((id) => [id, { x: seed.get(id).x, y: seed.get(id).y }]));
    for (let iter = 0; iter < EGO_FORCE_ITERATIONS; iter++) {
      const force = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
      for (let i = 0; i < ids.length; i++) {
        const a = pos.get(ids[i]);
        for (let j = i + 1; j < ids.length; j++) {
          const b = pos.get(ids[j]);
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) d2 = 1;
          const d = Math.sqrt(d2);
          const f = EGO_REPULSION / d2;
          const fx = (dx / d) * f, fy = (dy / d) * f;
          const fa = force.get(ids[i]), fb = force.get(ids[j]);
          fa.x += fx; fa.y += fy;
          fb.x -= fx; fb.y -= fy;
        }
      }
      for (const [aId, bId] of springs) {
        const a = pos.get(aId), b = pos.get(bId);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const fa = force.get(aId), fb = force.get(bId);
        if (fa) { fa.x += dx * EGO_SPRING; fa.y += dy * EGO_SPRING; }
        if (fb) { fb.x -= dx * EGO_SPRING; fb.y -= dy * EGO_SPRING; }
      }
      for (const id of ids) {
        if (id === fixedId) continue;
        const p = pos.get(id), f = force.get(id);
        p.x += f.x; p.y += f.y;
      }
    }
    return pos;
  }

  // pushes the (few) moved nodes' new positions into the GPU buffers directly — never a
  // full buildScene rebuild, so this stays well inside the 100ms budget below regardless of
  // total graph size (cost is O(moved), not O(49k)).
  function syncMovedInstancePositions(movedIds) {
    if (!mesh || !movedIds || !movedIds.size) return;
    const dummy = new THREE.Object3D();
    let touched = false;
    for (let i = 0; i < idToNode.length; i++) {
      const nd = idToNode[i];
      if (!movedIds.has(nd.id)) continue;
      dummy.position.set(nd.x || 0, nd.y || 0, (nd.radiusPx || 0) * 0.002);
      dummy.scale.setScalar(1);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      pickMesh.setMatrixAt(i, dummy.matrix);
      touched = true;
    }
    if (touched) { mesh.instanceMatrix.needsUpdate = true; pickMesh.instanceMatrix.needsUpdate = true; }
  }

  // ---- THE DRILL (operator ruling d7d55257, Thoth mail 11048) ----------------------------
  // A CONTAINER is any object whose own structural (containment/membership) degree exceeds
  // MAX_EGO_NODES -- a project, an agent with a huge working set, any hub the ordinary ego
  // walk could never show in full. Until Khnum's own `container` flag lands in
  // link_type_class, "structural" stands in for it (his own note, same mail) -- the exact
  // set every other container-shaped check in this file already uses. Focusing a container
  // is a DRILL, not the ordinary ego tree: the hub plus one count node per member type,
  // sorted by count, real members hidden until a reader clicks a type open. Acceptance:
  // "focusing repo:osiris opens under 30 nodes" -- confirmed live, see the tip's own commit.
  function containerMembersByType(id) {
    const byType = new Map(); // type -> nd[]
    for (const e of edges) {
      if (e.edgeClass !== "structural") continue;
      const other = e.source === id ? e.target : e.target === id ? e.source : null;
      if (other == null) continue;
      const nd = idById.get(other);
      if (!nd) continue;
      (byType.get(nd.type) || (byType.set(nd.type, []), byType.get(nd.type))).push(nd);
    }
    return byType;
  }
  function isContainerFocus(id) {
    let n = 0;
    for (const e of edges) {
      if (e.edgeClass !== "structural") continue;
      if (e.source === id || e.target === id) { n++; if (n > MAX_EGO_NODES) return true; }
    }
    return false;
  }

  let drillContainerId = null;
  let drillMembersByType = null;
  let drillExpandedType = null; // the one type currently paged open, or null
  let drillPageCount = 1; // pages of DRILL_PAGE_SIZE shown for drillExpandedType
  const DRILL_PAGE_SIZE = 50;
  let drillNodeEntries = []; // [{key, kind:'type'|'more', type, count, x, y, div}]
  function disposeDrillDivs() {
    for (const e of drillNodeEntries) e.div.remove();
    drillNodeEntries = [];
  }
  function clearDrillState() {
    disposeDrillDivs();
    drillContainerId = null;
    drillMembersByType = null;
    drillExpandedType = null;
    drillPageCount = 1;
  }
  function buildDrillDivs() {
    for (const entry of drillNodeEntries) {
      const div = document.createElement("div");
      div.className = "lod-glyph-label ego-drill-label";
      div.style.cursor = "pointer";
      div.textContent = entry.kind === "more"
        ? `+${entry.count} more ${entry.type}`
        : `${entry.type} ${entry.count}`;
      div.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (entry.kind === "more") { drillPageCount++; } else { drillExpandedType = entry.type; drillPageCount = 1; }
        renderContainerDrill(drillContainerId, { skipStackPush: true });
      });
      labelsEl.appendChild(div);
      entry.div = div;
    }
  }
  function positionDrillDivs() {
    for (const entry of drillNodeEntries) {
      if (!entry.div) continue;
      _screenV.set(entry.x, entry.y, 0).project(camera);
      entry.div.style.left = `${(_screenV.x * 0.5 + 0.5) * wrap.clientWidth}px`;
      entry.div.style.top = `${(-_screenV.y * 0.5 + 0.5) * wrap.clientHeight}px`;
    }
  }
  // "top 50 by degree then recency" -- recency isn't on the wire (fetchStreamSnapshot's own
  // node shape carries no timestamp), so degree desc with a stable id tiebreak stands in
  // until a real recency field exists to sort by; noted rather than faked.
  async function renderContainerDrill(id, opts) {
    const t0 = performance.now();
    const options = opts || {};
    const restored = egoSaved ? new Set(egoSaved.keys()) : null;
    restoreEgoLayout();
    if (restored) syncMovedInstancePositions(restored);
    disposeProjectAnchors(); // a drill replaces the normal focus view entirely
    clearDrillState();
    selectedId = id;
    pathFocusId = id;
    drillContainerId = id;
    drillMembersByType = containerMembersByType(id);
    if (!options.skipStackPush) pushFocusStack(id);
    if (onFocus) onFocus(id);

    const hub = idById.get(id);
    const cx = hub.x || 0, cy = hub.y || 0;
    const wpp = maxViewSize / wrap.clientHeight;
    const ringR = EGO_COL_SPACING_PX * wpp;

    pathReachable = new Set([id]);
    egoSaved = new Map();
    const seed = new Map([[id, { x: cx, y: cy }]]);
    const springs = [];
    const typeEntries = [...drillMembersByType.entries()].sort((a, b) => b[1].length - a[1].length);
    const angleStep = typeEntries.length ? (2 * Math.PI / typeEntries.length) : 0;

    typeEntries.forEach(([type, members], i) => {
      const angle = i * angleStep;
      const tx = cx + Math.cos(angle) * ringR, ty = cy + Math.sin(angle) * ringR;
      if (type === drillExpandedType) {
        const ranked = members.slice().sort((a, b) =>
          (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1));
        const budget = MAX_EGO_NODES - pathReachable.size;
        const take = Math.min(ranked.length, DRILL_PAGE_SIZE * drillPageCount, Math.max(0, budget));
        for (let k = 0; k < take; k++) {
          const nd = ranked[k];
          pathReachable.add(nd.id);
          egoSaved.set(nd.id, { x: nd.x, y: nd.y });
          const a2 = angle + (k - (take - 1) / 2) * 0.12;
          seed.set(nd.id, { x: cx + Math.cos(a2) * ringR * 1.8, y: cy + Math.sin(a2) * ringR * 1.8 });
          springs.push([id, nd.id]);
        }
        if (ranked.length > take) {
          const key = `more:${type}`;
          seed.set(key, { x: tx, y: ty });
          springs.push([id, key]);
          drillNodeEntries.push({ key, kind: "more", type, count: ranked.length - take, x: tx, y: ty, div: null });
        }
      } else {
        const key = `type:${type}`;
        seed.set(key, { x: tx, y: ty });
        springs.push([id, key]);
        drillNodeEntries.push({ key, kind: "type", type, count: members.length, x: tx, y: ty, div: null });
      }
    });

    const relaxed = relaxPositions(seed, springs, id);
    for (const [key, p] of relaxed) {
      if (key === id) continue;
      const nd = idById.get(key);
      if (nd) { nd.x = p.x; nd.y = p.y; continue; }
      const entry = drillNodeEntries.find((e) => e.key === key);
      if (entry) { entry.x = p.x; entry.y = p.y; }
    }
    syncMovedInstancePositions(new Set(egoSaved.keys()));
    buildDrillDivs();

    let minX = cx, maxX = cx, minY = cy, maxY = cy;
    for (const rid of pathReachable) {
      const nd = idById.get(rid);
      if (!nd) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    for (const e of drillNodeEntries) {
      minX = Math.min(minX, e.x); maxX = Math.max(maxX, e.x);
      minY = Math.min(minY, e.y); maxY = Math.max(maxY, e.y);
    }
    camera.position.x = (minX + maxX) / 2;
    camera.position.y = (minY + maxY) / 2;
    const span = Math.max(maxX - minX, maxY - minY, 0);
    const EGO_FIT_MIN_VIEWSIZE = 400;
    viewSize = Math.max(EGO_FIT_MIN_VIEWSIZE, Math.min(maxViewSize, span * 1.6 + 40));
    updateFrustum();
    rescaleForZoom();

    applyDim();
    buildEdgeLines(idToNode, edges);
    updatePathEdges();
    setStatus(`container: ${drillMembersByType.size} member types, ` +
      `${pathReachable.size - 1} shown`);
    scheduleLabelPick();
    markDirty();
    if (window.__spaceDebugTiming) console.debug("renderContainerDrill sync ms:", performance.now() - t0);
    await inspect(id);
  }

  // THE DRILL, items 2/4: "the walk stops at a container... containers passed through are
  // single anchor nodes, so a thread-to-decision walk across two projects reads as two
  // anchors and the path." The ordinary ego walk (bfsHops over outAdjPath/inAdjPath) only
  // ever follows PATH_EDGE_TYPES, never structural/container edges, so it already never
  // walks INTO a container -- nothing to enforce there. What's new: when the reachable set
  // spans more than one real project, each distinct project gets one small anchor label
  // (not per member) so a cross-project path still reads as "which project is this part of"
  // at a glance; clicking an anchor drills into that project's own container view.
  let projectAnchorEntries = [];
  function disposeProjectAnchors() {
    for (const e of projectAnchorEntries) e.div.remove();
    projectAnchorEntries = [];
  }
  function buildProjectObjectIndex() {
    projectObjectByName = new Map();
    for (const nd of idToNode) {
      if (nd.type === "SoftwareProject" && nd.label) projectObjectByName.set(`repo:${nd.label}`, nd.id);
    }
  }
  function buildProjectAnchors(focusId) {
    disposeProjectAnchors();
    const byProject = new Map(); // project name -> {sumX,sumY,count}
    for (const rid of pathReachable) {
      const nd = idById.get(rid);
      if (!nd || !nd.project || nd.project === "unfiled") continue;
      const agg = byProject.get(nd.project) || { sumX: 0, sumY: 0, count: 0 };
      agg.sumX += nd.x || 0; agg.sumY += nd.y || 0; agg.count++;
      byProject.set(nd.project, agg);
    }
    if (byProject.size < 2) return; // one project (or none) -- nothing to distinguish
    for (const [proj, agg] of byProject) {
      const x = agg.sumX / agg.count, y = agg.sumY / agg.count;
      const targetId = projectObjectByName.get(proj);
      const div = document.createElement("div");
      div.className = "lod-glyph-label ego-drill-label";
      div.style.cursor = targetId ? "pointer" : "default";
      div.textContent = `${proj} (${agg.count})`;
      if (targetId) {
        div.addEventListener("click", (ev) => { ev.stopPropagation(); focusObject(targetId); });
      }
      labelsEl.appendChild(div);
      projectAnchorEntries.push({ x, y, div });
    }
  }
  function positionProjectAnchors() {
    for (const e of projectAnchorEntries) {
      _screenV.set(e.x, e.y, 0).project(camera);
      e.div.style.left = `${(_screenV.x * 0.5 + 0.5) * wrap.clientWidth}px`;
      e.div.style.top = `${(-_screenV.y * 0.5 + 0.5) * wrap.clientHeight}px`;
    }
  }

  // THE DRILL, item 5 (Thoth mail 11048): "under a project filter a visible node with
  // hidden cross-project links shows a small counted stub; clicking the stub reveals that
  // project's part of the path without unhiding the project." Computed once per filter
  // change (setHiddenProjects calls buildProjectStubs), never per-frame -- a full edge
  // scan is a rare, deliberate act's cost, not a render one.
  //
  // MAX_PROJECT_STUBS: THE LAST RENDERER's own live verification (repo:osiris filtered
  // to itself, mail 11222) caught a real freeze here -- osiris's own cross-project fan-out
  // (works_in: 8,363 edges) produced 12,273 distinct (node, hiddenProject) boundary pairs,
  // one real DOM div EACH, repositioned via positionProjectStubs() on EVERY render frame.
  // That's the same declutter problem labels already solve (pickLabels' own top-N-by-
  // degree-in-viewport): keep the biggest, most-informative stubs, drop the rest, same as
  // the doc comment above already promises ("a small counted stub") but the code never
  // actually bounded.
  const MAX_PROJECT_STUBS = 80;
  let projectStubEntries = []; // [{nodeId, hiddenProject, count, div}]
  function disposeProjectStubs() {
    for (const e of projectStubEntries) e.div.remove();
    projectStubEntries = [];
  }
  function buildProjectStubDivs() {
    for (const entry of projectStubEntries) {
      const div = document.createElement("div");
      div.className = "lod-glyph-label ego-drill-label";
      div.style.cursor = "pointer";
      div.textContent = `+${entry.count} in ${entry.hiddenProject}`;
      div.addEventListener("click", (ev) => { ev.stopPropagation(); revealProjectStub(entry); });
      labelsEl.appendChild(div);
      entry.div = div;
    }
  }
  function buildProjectStubs() {
    disposeProjectStubs();
    if (hiddenProjects.size === 0) return;
    const stubs = new Map(); // nodeId -> Map<hiddenProjectName, count>
    for (const e of edges) {
      const na = idById.get(e.source), nb = idById.get(e.target);
      if (!na || !nb) continue;
      const aHidden = hiddenProjects.has(na.project), bHidden = hiddenProjects.has(nb.project);
      if (aHidden === bHidden) continue; // both or neither hidden -- not a filter boundary
      const visible = aHidden ? nb : na, hiddenNd = aHidden ? na : nb;
      if (!nodeVisible(visible)) continue; // the visible side must actually be shown itself
      const byProj = stubs.get(visible.id) || new Map();
      byProj.set(hiddenNd.project, (byProj.get(hiddenNd.project) || 0) + 1);
      stubs.set(visible.id, byProj);
    }
    const all = [];
    for (const [nodeId, byProj] of stubs) {
      for (const [hiddenProject, count] of byProj) {
        all.push({ nodeId, hiddenProject, count, div: null });
      }
    }
    all.sort((a, b) => b.count - a.count);
    projectStubEntries = all.slice(0, MAX_PROJECT_STUBS);
    buildProjectStubDivs();
  }
  function positionProjectStubs() {
    for (const entry of projectStubEntries) {
      if (!entry.div) continue;
      const nd = idById.get(entry.nodeId);
      entry.div.hidden = !nd || !nodeVisible(nd);
      if (entry.div.hidden) continue;
      _screenV.set(nd.x || 0, nd.y || 0, 0).project(camera);
      entry.div.style.left = `${(_screenV.x * 0.5 + 0.5) * wrap.clientWidth}px`;
      entry.div.style.top = `${(-_screenV.y * 0.5 + 0.5) * wrap.clientHeight}px`;
    }
  }
  // reveals just the one hidden-project neighbourhood a stub named -- adds those specific
  // ids to revealedStubIds (nodeVisible/applyDim's own override), never touches
  // hiddenProjects itself, so the rest of that project stays hidden.
  function revealProjectStub(entry) {
    const anchor = idById.get(entry.nodeId);
    if (!anchor) return;
    for (const e of edges) {
      const na = idById.get(e.source), nb = idById.get(e.target);
      if (!na || !nb) continue;
      if (na.id === entry.nodeId && nb.project === entry.hiddenProject) revealedStubIds.add(nb.id);
      if (nb.id === entry.nodeId && na.project === entry.hiddenProject) revealedStubIds.add(na.id);
    }
    applyDim();
    buildEdgeLines(idToNode, edges);
    buildProjectStubs(); // this stub's own count may now be satisfied and disappear
    markDirty();
  }

  // a second LineSegments drawn OVER the dim base edges: the reachable PATH edges (bright,
  // WITH DIRECTION — a vertex-colour gradient, brighter at the source/dependent end, dimmer
  // at the target/depended-on end, per Osiris's own from_id->to_id convention). Ruling
  // c5953bb1's own "the focused object's structural edges draw on focus only" carve-out is
  // GONE (THE LAST RENDERER, Thoth mail 11066: "an edge draws only when both ends are
  // visible, no structural-hop exception") -- it used to draw every structural edge
  // touching the focus regardless of whether the other end was ever positioned or visible,
  // which for a container-scale focus (repo:osiris, ~20k structural neighbours) meant
  // thousands of lines fanning to scattered original positions, "a solid disc of edges."
  // A structural edge among the reachable set (e.g. a drill's own hub-to-member link) still
  // draws through the ordinary base layer (buildEdgeLines) if the legend's own structural
  // checkbox is opted back in -- no separate exception needed or wanted any more.
  let pathHighlightEdges = null;
  const PATH_EDGE_BRIGHT = new THREE.Color(0x58a6ff);
  const PATH_EDGE_DIM = new THREE.Color(0x58a6ff).multiplyScalar(0.35);
  function updatePathEdges() {
    if (pathHighlightEdges) {
      scene.remove(pathHighlightEdges);
      pathHighlightEdges.geometry.dispose();
      pathHighlightEdges.material.dispose();
      pathHighlightEdges = null;
    }
    markDirty();
    if (!pathFocusId) return;
    const pos = [], col = [];
    for (const e of edges) {
      const onPath = PATH_EDGE_TYPES.has(e.type) && pathReachable.has(e.source) && pathReachable.has(e.target);
      if (!onPath) continue;
      const a = idById.get(e.source), b = idById.get(e.target);
      if (!a || !b) continue;
      pos.push(a.x || 0, a.y || 0, -0.05, b.x || 0, b.y || 0, -0.05);
      col.push(PATH_EDGE_BRIGHT.r, PATH_EDGE_BRIGHT.g, PATH_EDGE_BRIGHT.b,
        PATH_EDGE_DIM.r, PATH_EDGE_DIM.g, PATH_EDGE_DIM.b);
    }
    if (!pos.length) return;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(pos), 3));
    geo.setAttribute("color", new THREE.Float32BufferAttribute(new Float32Array(col), 3));
    pathHighlightEdges = new THREE.LineSegments(
      geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85 }));
    scene.add(pathHighlightEdges);
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
    // no scheduleLabelUpdate() here — label POSITIONS are repainted on every render this
    // markDirty() triggers (render-on-demand, see the loop below); only WHICH labels show
    // is still debounced (scheduleLabelPick).
    markDirty();
    scheduleLabelPick();
  });

  // O(1) regardless of node count now — sizing lives in the shader (aRadiusPx * a live
  // uWorldPerPx uniform, see makeInstancedCircleMaterial), so a zoom step only ever writes
  // one float per material (the draw mesh's and pick mesh's own uWorldPerPx) instead of
  // rewriting 49,019 instance matrices.
  function rescaleForZoom() {
    const wpp = worldPerPx();
    if (meshUniforms) meshUniforms.uWorldPerPx.value = wpp;
    if (pickUniforms) pickUniforms.uWorldPerPx.value = wpp;
    // no edge-style call here any more — the edge-fade shader (makeEdgeFadeMaterial) reads
    // screen length straight off projectionMatrix/modelViewMatrix every render, already
    // current every frame with zero extra work on a zoom step.
  }

  // wheel = LOOKING ONLY, cursor-anchored (Thoth's own live fix, mail 10581 item 5: "zoom
  // is not anchored at the cursor"), coalesced to one update per animation frame no matter
  // how many wheel events land in that frame (a real trackpad/mouse burst is 20-60 events —
  // each one used to trigger its own full rescale; now each just accumulates a delta, and
  // ONE zoomAt() runs per frame).
  let pendingWheelDelta = 0, wheelClientX = 0, wheelClientY = 0, wheelRafPending = false;
  function zoomAt(clientX, clientY, deltaY) {
    const rect = wrap.getBoundingClientRect();
    const nx = rect.width ? (clientX - rect.left) / rect.width : 0.5;
    const ny = rect.height ? (clientY - rect.top) / rect.height : 0.5;
    const worldX = camera.position.x + THREE.MathUtils.lerp(camera.left, camera.right, nx);
    const worldY = camera.position.y + THREE.MathUtils.lerp(camera.top, camera.bottom, ny);
    viewSize = Math.max(minViewSize, Math.min(maxViewSize, viewSize * Math.exp(deltaY * 0.001)));
    updateFrustum();
    // re-anchor: keep the same world point under the cursor after the frustum resize.
    camera.position.x = worldX - THREE.MathUtils.lerp(camera.left, camera.right, nx);
    camera.position.y = worldY - THREE.MathUtils.lerp(camera.top, camera.bottom, ny);
    rescaleForZoom();
    scheduleLabelPick();
    markDirty();
  }
  function applyPendingWheel() {
    wheelRafPending = false;
    if (pendingWheelDelta === 0) return;
    const deltaY = pendingWheelDelta;
    pendingWheelDelta = 0;
    zoomAt(wheelClientX, wheelClientY, deltaY);
  }
  renderer.domElement.addEventListener(
    "wheel",
    (ev) => {
      ev.preventDefault();
      pendingWheelDelta += ev.deltaY;
      wheelClientX = ev.clientX; wheelClientY = ev.clientY;
      if (!wheelRafPending) { wheelRafPending = true; requestAnimationFrame(applyPendingWheel); }
    },
    { passive: false }
  );

  // ---- GPU picking (nearest-within-tolerance, review flaw #3/#8) ----------------------
  // review flaw #3: the original 1x1 pick was EXACT-PIXEL, no tolerance -- two real clicks
  // on genuinely visible small nodes (4px, 8px) missed outright (selectedId/pickAt both
  // null). Same root cause made the hover card read empty on a real hover (#8): it calls
  // this same function. Fixed by rendering a small box around the cursor instead of one
  // pixel and picking whichever hit id sits closest to the box's own centre — an exact hit
  // still wins immediately (distance 0), a near-miss within PICK_BOX/2 px now resolves too.
  const PICK_BOX = 33; // device px, odd -- generous tolerance, still a trivial GPU readback
  const PICK_HALF = (PICK_BOX - 1) / 2;
  const pickTarget = new THREE.WebGLRenderTarget(PICK_BOX, PICK_BOX);
  const pickBuf = new Uint8Array(PICK_BOX * PICK_BOX * 4);
  function pickAt(clientX, clientY) {
    const rect = renderer.domElement.getBoundingClientRect();
    // setViewOffset's (x,y) origin is TOP-LEFT (matching a mouse event's own coordinates,
    // per its own docstring's tile-grid example: A/B/C at y=0 sit ABOVE D/E/F at y=h) —
    // NOT WebGL's bottom-left convention. The earlier flip here was the real cause of
    // clicks missing the node visually under the cursor.
    const px = (clientX - rect.left) * (window.devicePixelRatio || 1);
    const py = (clientY - rect.top) * (window.devicePixelRatio || 1);
    camera.setViewOffset(
      renderer.domElement.width, renderer.domElement.height,
      px - PICK_HALF, py - PICK_HALF, PICK_BOX, PICK_BOX);
    renderer.setRenderTarget(pickTarget);
    renderer.render(pickScene, camera);
    renderer.setRenderTarget(null);
    camera.clearViewOffset();
    renderer.readRenderTargetPixels(pickTarget, 0, 0, PICK_BOX, PICK_BOX, pickBuf);
    let bestId = 0, bestDist = Infinity;
    for (let y = 0; y < PICK_BOX; y++) {
      for (let x = 0; x < PICK_BOX; x++) {
        const o = (y * PICK_BOX + x) * 4;
        const id = pickBuf[o] | (pickBuf[o + 1] << 8) | (pickBuf[o + 2] << 16);
        if (id === 0) continue;
        const dx = x - PICK_HALF, dy = y - PICK_HALF;
        const dist = dx * dx + dy * dy;
        if (dist < bestDist) { bestDist = dist; bestId = id; }
      }
    }
    return bestId === 0 ? null : idToNode[bestId - 1];
  }

  // TIP 1 AMENDMENT (operator via Thoth mail 10726): a single CLICK on a node IS focus —
  // select, inspector, hide, fit, one gesture. No double-click, no Enter-to-promote; a click
  // on empty canvas still clears. Back/Escape are the only acts left that navigate history.
  // TIP 4 (operator ruling "DENSITY NOT DISCS", mail 11011) simplifies this back to the
  // pre-TIP-3 shape: every object is drawn (and so pickable, same GPU pick pass) at every
  // zoom now, no separate glyph layer or drill-in branch to special-case any more.
  renderer.domElement.addEventListener("click", (ev) => {
    if (dragDistance > CLICK_SLOP_PX) return; // the trailing click after a real pan/drag
    const hit = pickAt(ev.clientX, ev.clientY);
    if (hit) focusObject(hit.id);
    else clearFocus();
  });

  // TIP 1(c): the hover card shows the label line plus type and project — the inspector
  // (click) has the rest. Debounced like the label pick (not every mousemove — pickAt is a
  // real render-target pass, cheap once, not something to run at full mouse-event rate) and
  // skipped entirely while dragging so it never fights a pan.
  let hoverNode = null;
  let hoverTimer = null;
  const hoverEl = document.createElement("div");
  hoverEl.className = "hover-card";
  hoverEl.hidden = true;
  wrap.appendChild(hoverEl);
  function updateHoverCard(nd) {
    hoverEl.innerHTML = `<div class="hover-label">${labelTextFor(nd)}</div>` +
      `<div class="hover-meta">${nd.type}${nd.project ? " · " + nd.project : ""}</div>`;
  }
  function positionHoverCard(clientX, clientY) {
    const rect = wrap.getBoundingClientRect();
    hoverEl.style.left = `${clientX - rect.left + 14}px`;
    hoverEl.style.top = `${clientY - rect.top + 14}px`;
  }
  renderer.domElement.addEventListener("mousemove", (ev) => {
    if (dragging) { hoverEl.hidden = true; hoverNode = null; return; }
    positionHoverCard(ev.clientX, ev.clientY);
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      const hit = pickAt(ev.clientX, ev.clientY);
      hoverNode = hit || null;
      if (hit) { updateHoverCard(hit); hoverEl.hidden = false; } else { hoverEl.hidden = true; }
    }, 80);
  });
  renderer.domElement.addEventListener("mouseleave", () => {
    clearTimeout(hoverTimer);
    hoverEl.hidden = true;
    hoverNode = null;
  });

  function clearFocus() {
    selectedId = null;
    pathFocusId = null;
    pathReachable = new Set();
    const restored = egoSaved ? new Set(egoSaved.keys()) : null;
    restoreEgoLayout();
    if (restored) syncMovedInstancePositions(restored);
    clearDrillState();
    disposeProjectAnchors();
    applyDim();
    // review flaw #1: the BASE edge layer is its own static geometry (built once from
    // node x/y at buildEdgeLines time) -- hiding a NODE's own instance (aVisible=0) never
    // touched its EDGES, so the whole unreachable graph stayed drawn in faint lines after a
    // focus. Rebuilding here (nodeVisible already gates on pathReachable/hiddenNodeTypes)
    // is what actually drops them; clearing needs it too, to restore the full base layer.
    buildEdgeLines(idToNode, edges);
    updatePathEdges();
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
  if (backBtn) backBtn.addEventListener("click", goBack);
  // TIP 1 AMENDMENT: the downstream toggle (was "Widen" — depth is unlimited by default
  // now, so raising a cap is moot). Off by default; re-runs the current focus on toggle.
  if (downstreamBtn) {
    downstreamBtn.textContent = "Downstream: off";
    downstreamBtn.addEventListener("click", () => {
      includeDownstream = !includeDownstream;
      downstreamBtn.textContent = `Downstream: ${includeDownstream ? "on" : "off"}`;
      if (pathFocusId) focusObject(pathFocusId, { skipStackPush: true });
    });
  }
  function pushFocusStack(id) {
    if (focusStack[focusStack.length - 1] === id) return;
    focusStack.push(id);
    if (focusStack.length > 50) focusStack.shift();
  }
  function goBack() {
    if (focusStack.length < 2) { clearFocus(); return; }
    focusStack.pop(); // the current focus
    const prev = focusStack[focusStack.length - 1];
    focusObject(prev, { skipStackPush: true });
  }

  // ---- FOCUS = THE TREE TO SOURCE (ruling c5953bb1, amended by mail 10726): a single click
  // is the whole gesture now — select, inspector, hide, fit, all synchronous, all CLIENT-SIDE
  // off the already-loaded edge list (never a network wait; `inspect(id)`'s own fetch is
  // awaited LAST, below, and never gates any of this). Walks upstream by default until
  // roots, downstream only when toggled on, hides everything unreachable (TIP 1(d), no
  // dim), relays out the reachable set locally (TIP 1's own ego-layout amendment), fits the
  // camera, then the inspector fetch fills in after.
  async function focusObject(id, opts) {
    // review flaw #9: focusObject(null) (a stray call with no real hit — the footer's own
    // "focused-badge" onclick, or a failed pick) used to set pathFocusId=null yet still walk
    // and report "focused: 1 reachable" against a degenerate single-null-entry set.
    if (!id) { clearFocus(); return; }
    // THE DRILL (Thoth mail 11048): a container-scale focus never runs the ordinary ego
    // walk at all -- it would either blow straight past MAX_EGO_NODES or (worse, the
    // operator's own observed bug) silently truncate while updatePathEdges still drew every
    // one of the focus's own uncapped structural edges, "a solid disc."
    if (isContainerFocus(id)) { await renderContainerDrill(id, opts); return; }
    clearDrillState(); // leaving a drill (if any) for an ordinary small-object focus
    const t0 = performance.now();
    const options = opts || {};
    selectedId = id;
    pathFocusId = id;
    focusDepth = options.depth || FOCUS_DEPTH_DEFAULT;
    const hopsUp = bfsHops(outAdjPath, id, focusDepth);
    const hopsDown = includeDownstream ? bfsHops(inAdjPath, id, focusDepth) : new Map([[id, 0]]);
    pathReachable = new Set([...hopsUp.keys(), ...hopsDown.keys()]);
    // TIP 1(d)/amendment: "focus is never empty" — Thoth's own live measurement found a
    // degree-8 Decision with no PATH_EDGE_TYPES links reaching only itself and collapsing
    // the camera fit to a point. When the walk finds nothing beyond the focused node itself,
    // widen one hop over its own STRUCTURAL edges instead (ranked as upstream, hop 1, for
    // the ego layout below) — still just this node's real neighbours, never a synthetic
    // minimum. review flaw #2's OWN second half: this loop had no cap at all — a real hub
    // (repo:osiris, structural degree 20k+) has few/no PATH_EDGE_TYPES links of its own, so
    // pathReachable.size<=1 was true and this fallback alone reproduced the exact same
    // whole-graph blowup the rank cap above was built to prevent. Same MAX_EGO_NODES cap.
    if (pathReachable.size <= 1) {
      for (const e of edges) {
        if (pathReachable.size >= MAX_EGO_NODES) break;
        if (e.edgeClass !== "structural") continue;
        const other = e.source === id ? e.target : e.target === id ? e.source : null;
        if (other == null || pathReachable.has(other)) continue;
        pathReachable.add(other);
        hopsUp.set(other, 1);
      }
    }
    if (!options.skipStackPush) pushFocusStack(id);
    if (onFocus) onFocus(id); // shares the selection with an embedding table (console.js)

    // EGO RELAYOUT (mail 10726): focus at centre, ancestors ranked leftward by hop (roots
    // farthest left), downstream (if on) ranked rightward — "distance rational instead of
    // the world-unit spread." Moves only the reachable set's own GPU instances (O(moved)).
    applyEgoLayout(id, hopsUp, hopsDown);
    syncMovedInstancePositions(egoSaved ? new Set(egoSaved.keys()) : null);

    // zoom-to-fit: frame the camera around exactly the reachable set's own (now relaid-out)
    // bounding box, not a fixed small viewSize centered on the click.
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const rid of pathReachable) {
      const nd = idById.get(rid);
      if (!nd || nd.x == null || nd.y == null) continue;
      minX = Math.min(minX, nd.x); maxX = Math.max(maxX, nd.x);
      minY = Math.min(minY, nd.y); maxY = Math.max(maxY, nd.y);
    }
    if (Number.isFinite(minX)) {
      camera.position.x = (minX + maxX) / 2;
      camera.position.y = (minY + maxY) / 2;
      const span = Math.max(maxX - minX, maxY - minY, 0);
      // padding + a sane floor/ceiling — the ceiling rides maxViewSize (the real fitted
      // graph's own extent, set in fitToNodes) rather than a hardcoded 1300: the same class
      // of stale-constant bug Thoth caught in the wheel clamp (mail 10581) would otherwise
      // clip a legitimately wide-spread path back down to a fixed small view. The floor
      // (review flaw #6, TIP 1c) is raised from the old 30 -- fine for a whole-graph fit,
      // where span is always huge, but a tiny reachable set (a lone child or two) could
      // collapse the ego layout's own span near that floor, leaving the 48px screen CAP as
      // the dominant visual element in an otherwise near-empty frame.
      const EGO_FIT_MIN_VIEWSIZE = 400;
      viewSize = Math.max(EGO_FIT_MIN_VIEWSIZE, Math.min(maxViewSize, span * 1.6 + 40));
      updateFrustum();
      rescaleForZoom();
    }

    applyDim();
    buildEdgeLines(idToNode, edges); // review flaw #1: base layer must hide too, see clearFocus
    updatePathEdges();
    buildProjectAnchors(id);
    setStatus(`focused: ${pathReachable.size} reachable` +
      (includeDownstream ? " (upstream+downstream)" : " (upstream)"));
    scheduleLabelPick();
    markDirty();
    // TIP 1's own 100ms budget (mail 10726 item 2): everything above is client-side and
    // synchronous; only the inspector's own network fetch happens after, unawaited by the
    // visual. Logged, not asserted, since a live DevTools/CPU throttle can't be simulated
    // in a unit test — the discipline is the guarantee, not this one measurement.
    if (window.__spaceDebugTiming) console.debug("focusObject sync ms:", performance.now() - t0);
    await inspect(id);
  }

  async function inspect(id) {
    // review flaw #1 (TIP 1c, Thoth mail 10891): "the right pane must show the focused
    // object's details... today it stays empty after a click." Root cause: no response
    // check plus objectDetail() throwing synchronously (e.g. reading o.properties.some on
    // a malformed/error body) meant the `rightRail.innerHTML = ...` assignment never
    // happened at all — the rail silently kept whatever it showed BEFORE the click (the
    // "Click any object to inspect..." placeholder on a fresh session, read as "empty").
    // Every path below now writes something real to the rail, success or failure.
    let obj;
    try {
      const res = await fetch(`/objects/${id}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      obj = await res.json();
      rightRail.className = "rail";
      rightRail.innerHTML = Osiris.objectDetail(obj, "");
    } catch (err) {
      console.error("inspect() failed for", id, err);
      rightRail.className = "rail";
      rightRail.innerHTML = `<div class="insp-empty">Could not load ${id.slice(0, 8)}: ` +
        `${(err && err.message) || err}</div>`;
      return;
    }
    // the inspector's own Focus button — a re-focus shortcut, now that a plain click on
    // the canvas already IS focus (TIP 1 amendment retired the old double-click/Enter
    // triggers this button used to sit alongside).
    const focusBtn = document.createElement("button");
    focusBtn.className = "iconbtn";
    focusBtn.textContent = pathFocusId === id ? "Focused" : "Focus";
    focusBtn.style.cssText = "margin-bottom:10px";
    focusBtn.addEventListener("click", () => focusObject(id));
    rightRail.prepend(focusBtn);
    // every object reference in the inspector (upstream_ids, readers, links) walks the
    // focus — ruling c5953bb1's own "harmony" requirement, part C, but the wiring lives
    // here since it's the same click-through this inspector has always used.
    const relsEl = rightRail.querySelector("[data-rels]");
    if (relsEl) {
      try {
        await Osiris.loadRels(relsEl, id, (pickId) => focusObject(pickId), () => {});
      } catch (err) {
        console.error("loadRels() failed for", id, err);
      }
    }
  }

  // TIP 1 AMENDMENT (mail 10726): "no double-click or Enter" — a click already IS focus,
  // so the old Enter-promotes-selection listener (and the dblclick listener above it) are
  // retired outright, not left as harmless redundancy.

  // TIP 1(e), amended: ONE search, ONE gesture — the in-canvas "Find a node" box is gone;
  // the header omnibox (console.js's own runOmniSearch/execOmniItem) drives the graph
  // directly now, a hit always focuses (click and Enter no longer differ, matching the
  // canvas's own "one gesture" — see mail 10726).

  // TIP 1(c), TIP 1b (Thoth mail 10755): LABELS ARE NAMES — Agent by handle/name,
  // SoftwareProject by repo name, Person by name, everything else type + short title. One
  // line, hard-truncated at 40 chars with an ellipsis. Khnum's own `labels` wire header
  // (tip 2g, fixed live in mail 10892/commit 0496a7d to resolve a real summary/title/
  // subject/name assertion for every type — a Commit's own label now carries its real
  // subject line straight off the wire) computes exactly this rule server-side,
  // index-aligned to object_ids — nd.label is already the final text, synchronous, no
  // per-node network fetch for any type. The earlier Commit-only client-side upgrade
  // (fetchCommitSubject, review flaw #6) is retired outright now that the gap it patched
  // is closed at the source.
  function fallbackLabel(nd) { return `${nd.type} ${nd.id.slice(0, 8)}`; }
  function labelTextFor(nd) { return nd.label || fallbackLabel(nd); }

  // THE LAST RENDERER (operator ruling d7d55257, Thoth mail 11066): "labels for the top-N
  // objects by degree inside the current viewport, de-overlapped, at every zoom." No
  // separate project-label pass any more -- a project's own name just IS whatever object in
  // it has the highest degree in view. Candidacy is nodeVisible(nd) (already the single
  // source of truth for hidden types/hidden projects/focus-reachability everywhere else in
  // this file) intersected with the camera's own world-space frustum bounds -- a node must
  // genuinely be on screen to be a label candidate, not merely "near the camera centre" (the
  // old rule, which could label something off past the edge of the viewport).
  const N_LABELS = 40;
  let labeledNodes = [];
  const labelDivs = new Map(); // node -> div, reused across frames instead of rebuilt
  let labelPickTimer = null;
  function scheduleLabelPick() {
    if (labelPickTimer) return;
    labelPickTimer = setTimeout(() => { labelPickTimer = null; pickLabels(); }, 150);
  }
  function pickLabels() {
    const halfW = (camera.right - camera.left) / 2, halfH = (camera.top - camera.bottom) / 2;
    const minX = camera.position.x - halfW, maxX = camera.position.x + halfW;
    const minY = camera.position.y - halfH, maxY = camera.position.y + halfH;
    const pool = idToNode.filter((nd) =>
      nodeVisible(nd) && nd.x >= minX && nd.x <= maxX && nd.y >= minY && nd.y <= maxY);
    labeledNodes = pool.slice()
      .sort((a, b) => (b.degree || 0) - (a.degree || 0))
      .slice(0, N_LABELS);
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
      div.textContent = labelTextFor(nd); // fallback text now, swapped for the real name async
      labelsEl.appendChild(div);
      labelDivs.set(nd, div);
    }
    markDirty(); // newly (un)labeled divs need one more positionLabels() pass to place them
  }
  // runs on every render (render-on-demand now, not an unconditional per-frame loop — see
  // below) — cheap (one project() + style write per already-chosen label, no sort, no DOM
  // create/destroy) so labels track the scene with zero perceptible lag whenever it fires.
  // DECLUTTER: "present text without it looking like garbage" — labeledNodes is already
  // nearest-to-camera-first (from pickLabels' own sort), so a plain greedy pass — show a
  // label unless its screen box would overlap one already placed this frame — keeps the
  // closest/most-relevant labels and silently drops the rest, rather than stacking dozens
  // of overlapping strings into an unreadable wall of text.
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
    // THE LAST RENDERER: object titles show at every zoom now, no tier gate -- the same
    // top-N-by-degree-in-viewport pool pickLabels() computed applies universally.
    for (const nd of labeledNodes) {
      const div = labelDivs.get(nd);
      if (!div) continue;
      _screenV.set(nd.x || 0, nd.y || 0, 0).project(camera);
      const x = (_screenV.x * 0.5 + 0.5) * wrap.clientWidth;
      const y = (-_screenV.y * 0.5 + 0.5) * wrap.clientHeight;
      const lit = nd.id === pathFocusId || pathReachable.has(nd.id) || nd.id === selectedId;
      // lit/focused labels always win their spot (never declutter the thing you asked to
      // see); ordinary labels yield to anything already placed.
      if (!lit && overlapsPlaced(x, y)) { div.hidden = true; continue; }
      div.hidden = false;
      div.style.left = `${x}px`;
      div.style.top = `${y}px`;
      div.className = "lbl" + (lit ? " lit" : "");
      _placed.push([x - LABEL_W / 2, y - LABEL_H, x + LABEL_W / 2, y]);
    }
    positionDrillDivs();
    positionProjectAnchors();
    positionProjectStubs();
  }

  // the render loop itself is defined above (markDirty/renderIfDirty, right after the
  // camera/resize setup) — render-on-demand per Thoth's own live measurement (mail 10581):
  // the old unconditional rAF-plus-50ms-fallback loop rendered forever regardless of
  // whether anything changed or the tab/surface was even visible, which is real waste this
  // fix removes rather than papering over.
  pickLabels();
  markDirty();

  const api = {
    focusObject, clearFocus, inspect, pause, resume, goBack, setHiddenTypes,
    setHiddenProjects,
    get idToNode() { return idToNode; },
    get pathReachable() { return pathReachable; },
    get pathFocusId() { return pathFocusId; },
    get selectedId() { return selectedId; },
    camera, pickAt, mesh: () => mesh, worldPerPx, nodeScreenPx, renderer,
    // debug/test hooks only (same convention as window.__space always being exposed) —
    // zoomAt bypasses the rAF-coalesced wheel path for direct exercise; forceRender skips
    // the dirty check for a synchronous frame.
    zoomAt, forceRender: () => { renderScene(); positionLabels(); },
    // live-verification/test hooks for the top-N-by-degree label pool -- same "debug hooks
    // alongside the real api" convention as zoomAt/forceRender above.
    get toneMapActive() { return !!sceneTarget; },
    get labeledNodeCount() { return labeledNodes.length; },
    get visibleLabelCount() { return [...labelDivs.values()].filter((d) => !d.hidden).length; },
    // THE DRILL (Thoth mail 11048): live-verification/test hooks for the container drill,
    // cross-project anchors, and the project-filter stub reveal.
    isContainerFocus, containerMembersByType,
    get drillContainerId() { return drillContainerId; },
    get drillNodeEntries() { return drillNodeEntries.map((e) => ({ key: e.key, kind: e.kind, type: e.type, count: e.count })); },
    get drillExpandedType() { return drillExpandedType; },
    expandDrillType(type) { drillExpandedType = type; drillPageCount = 1; return renderContainerDrill(drillContainerId, { skipStackPush: true }); },
    get projectAnchorCount() { return projectAnchorEntries.length; },
    get projectStubEntries() { return projectStubEntries.map((e) => ({ nodeId: e.nodeId, hiddenProject: e.hiddenProject, count: e.count })); },
    revealProjectStub,
  };
  window.__space = api; // kept for existing debugging/test scripts, same shape as before
  return api;
}
