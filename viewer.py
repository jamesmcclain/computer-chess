"""Read-only web viewer (port 5004).

Shows the current board state only — no controls, no way to submit
moves. Updates are pushed to the browser over Server-Sent Events (SSE)
rather than polled on a fixed interval: the server blocks (via
`ChessGame.wait_for_change`) until the game actually changes, then
streams the new state down an open `/events` connection. The client only
rewrites the board squares that actually changed piece, instead of
tearing down and rebuilding the whole board on every tick — that
per-square-teardown was the source of the old polling viewer's
"flashing" (recreated <img> tags briefly show a broken-image icon before
their onerror fallback kicks in, even when nothing on that square
changed).

SSE was chosen over WebSockets because this is a one-way, server-to-
client feed (the viewer can't send anything back) — a plain HTTP
streaming response covers that with no extra protocol, dependency, or
handshake, and works with Flask's built-in dev server out of the box.

Appearance (board squares + piece art) is a purely client-side, per-
browser preference: it's read from the theme catalogue below, picked
with on-page controls, and remembered in the browser's localStorage.
It never touches game state, so different people watching the same
game can each see their own board/piece styles.
"""

import json
import os

from flask import Flask, Response, jsonify, render_template_string, stream_with_context

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
BOARDS_CATALOGUE_PATH = os.path.join(STATIC_DIR, "chess", "boards", "squares_catalogue.json")
PIECES_CATALOGUE_PATH = os.path.join(STATIC_DIR, "chess", "pieces", "pieces_catalogue.json")

PIECE_TYPE_NAMES = {
    "P": "pawn", "N": "knight", "B": "bishop",
    "R": "rook", "Q": "queen", "K": "king",
}


def _stem_id(path):
    """'square_metal_dark.png' -> 'metal_dark'."""
    name = os.path.splitext(os.path.basename(path))[0]
    return name[len("square_"):] if name.startswith("square_") else name


def _label(text):
    return text[:1].upper() + text[1:]


def load_board_catalogue():
    """Returns {"sets": [...], "darks": [...], "lights": [...]}.

    `sets` pairs a dark+light texture as the theme's designer intended
    ("matched" mode). `darks`/`lights` are every dark/light option
    flattened out so "independent" mode can mix and match across sets.
    """
    if not os.path.isfile(BOARDS_CATALOGUE_PATH):
        return {"sets": [], "darks": [], "lights": []}

    with open(BOARDS_CATALOGUE_PATH) as f:
        data = json.load(f)

    sets, darks, lights, seen = [], [], [], set()
    for s in data.get("sets", []):
        dark_id = _stem_id(s["dark"]["path"])
        light_id = _stem_id(s["light"]["path"])
        dark = {"id": dark_id, "label": _label(s["dark"]["material"]),
                 "url": f"/static/chess/boards/{s['dark']['path']}"}
        light = {"id": light_id, "label": _label(s["light"]["material"]),
                  "url": f"/static/chess/boards/{s['light']['path']}"}
        sets.append({"id": s["id"], "label": s["label"], "dark": dark, "light": light})
        if dark_id not in seen:
            darks.append(dark)
            seen.add(dark_id)
        if light_id not in seen:
            lights.append(light)
            seen.add(light_id)
    return {"sets": sets, "darks": darks, "lights": lights}


def load_piece_catalogue():
    """Returns {"sets": [{"id", "label", "pieces": {"white": {"king": url, ...}, "black": {...}}}]}."""
    if not os.path.isfile(PIECES_CATALOGUE_PATH):
        return {"sets": []}

    with open(PIECES_CATALOGUE_PATH) as f:
        data = json.load(f)

    sets = []
    for s in data.get("sets", []):
        pieces = {"white": {}, "black": {}}
        for color in ("white", "black"):
            color_data = s.get("pieces", {}).get(color, {})
            for ptype, entry in color_data.items():
                pieces[color][ptype] = f"/static/chess/pieces/{entry['path']}"
        sets.append({"id": s["id"], "label": s["label"], "pieces": pieces})
    return {"sets": sets}


PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GNU Chess &mdash; board viewer</title>
<style>
  :root {
    --light: #f0d9b5; --dark: #b58863; --accent: #e0b84f;
    --panel-bg: #262626; --panel-border: #3a3a3a;
    --light-square-url: none; --dark-square-url: none;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #1e1e1e; color: #eee;
    margin: 0;
    display: flex; flex-direction: column; align-items: center;
    padding: 1.25rem 1rem 2rem;
    min-height: 100%;
  }
  h1 { font-size: 1rem; font-weight: 500; color: #999; margin: 0 0 0.75rem; letter-spacing: 0.02em; }
  #conn { font-size: 0.75rem; color: #666; margin-bottom: 0.5rem; }
  #conn.live::before { content: "\\25CF"; color: #4caf50; margin-right: 0.4em; }
  #conn.down::before { content: "\\25CF"; color: #b04a4a; margin-right: 0.4em; }
  #status { margin-bottom: 0.75rem; font-size: 1.1rem; min-height: 1.4em; transition: color 0.3s ease; }
  #status.over { color: var(--accent); font-weight: 600; }

  /* ---- style controls -------------------------------------------------- */
  #controls {
    display: flex; flex-wrap: wrap; gap: 0.6rem 1.2rem; justify-content: center;
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 10px; padding: 0.75rem 1rem; margin-bottom: 1rem;
    font-size: 0.8rem; max-width: 700px;
  }
  .ctrl-group { display: flex; flex-direction: column; gap: 0.3rem; min-width: 9rem; }
  .ctrl-group .ctrl-title {
    display: flex; align-items: center; justify-content: space-between;
    color: #aaa; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase; font-size: 0.68rem;
  }
  .mode-toggle { display: flex; gap: 2px; background: #1a1a1a; border-radius: 6px; padding: 2px; }
  .mode-toggle button {
    all: unset; cursor: pointer; font-size: 0.68rem; padding: 0.2rem 0.5rem;
    border-radius: 4px; color: #999;
  }
  .mode-toggle button.active { background: var(--accent); color: #1a1a1a; font-weight: 600; }
  .ctrl-row { display: flex; align-items: center; gap: 0.4rem; }
  .ctrl-row label { color: #888; width: 3rem; flex-shrink: 0; }
  select {
    background: #1a1a1a; color: #eee; border: 1px solid #444; border-radius: 6px;
    padding: 0.25rem 0.4rem; font-size: 0.78rem; width: 100%;
  }
  select:focus { outline: 1px solid var(--accent); }

  /* ---- board ------------------------------------------------------------
     Sized in JS (via ResizeObserver) to the largest square that fits the
     viewport, so it scales smoothly as the window is resized rather than
     snapping between a few fixed breakpoints. `will-change` + the null
     3D transform hint the browser to keep the board on its own GPU-
     composited layer, so the resize (and the per-piece pop-in animation)
     is pushed through hardware compositing instead of a CPU repaint every
     frame — the practical benefit a heavier engine like three.js would
     bring here, without standing up a WebGL context for what is
     fundamentally a flat 2D grid of images. */
  #board-wrap { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; width: 100%; min-height: 0; }
  #board {
    position: relative;
    display: grid;
    grid-template-columns: repeat(8, 1fr);
    grid-template-rows: repeat(8, 1fr);
    border: 4px solid #444;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    will-change: width, height;
    transform: translateZ(0);
  }
  .sq { display: flex; align-items: center; justify-content: center; font-size: min(8vw, 2.4rem); user-select: none; position: relative; background-size: cover; background-position: center; }
  .sq.light { background-color: var(--light); background-image: var(--light-square-url); }
  .sq.dark { background-color: var(--dark); background-image: var(--dark-square-url); }
  .sq .piece { width: 88%; height: 88%; object-fit: contain; animation: pop-in 0.18s ease-out; filter: drop-shadow(0 2px 3px rgba(0,0,0,0.5)); }
  .sq .piece.glyph { font-size: min(8vw, 2.4rem); line-height: 1; animation: pop-in 0.18s ease-out; filter: none; }
  @keyframes pop-in {
    from { opacity: 0; transform: scale(0.75); }
    to   { opacity: 1; transform: scale(1); }
  }
  #meta { margin-top: 1rem; font-size: 0.85rem; color: #888; text-align: center; max-width: 520px; line-height: 1.6; }
</style>
</head>
<body>
  <h1>GNU Chess &mdash; board viewer (read only)</h1>
  <div id="conn">connecting&hellip;</div>
  <div id="status"></div>

  <div id="controls">
    <div class="ctrl-group">
      <div class="ctrl-title">
        Board style
        <div class="mode-toggle" data-toggle="board">
          <button data-mode="matched" class="active">Matched</button>
          <button data-mode="independent">Split</button>
        </div>
      </div>
      <div data-panel="board-matched">
        <select id="board-set"></select>
      </div>
      <div data-panel="board-independent" style="display:none; gap:0.3rem; flex-direction:column;">
        <div class="ctrl-row"><label>Light</label><select id="board-light"></select></div>
        <div class="ctrl-row"><label>Dark</label><select id="board-dark"></select></div>
      </div>
    </div>

    <div class="ctrl-group">
      <div class="ctrl-title">
        Piece style
        <div class="mode-toggle" data-toggle="piece">
          <button data-mode="matched" class="active">Matched</button>
          <button data-mode="independent">Split</button>
        </div>
      </div>
      <div data-panel="piece-matched">
        <select id="piece-set"></select>
      </div>
      <div data-panel="piece-independent" style="display:none; gap:0.3rem; flex-direction:column;">
        <div class="ctrl-row"><label>White</label><select id="piece-white"></select></div>
        <div class="ctrl-row"><label>Black</label><select id="piece-black"></select></div>
      </div>
    </div>
  </div>

  <div id="board-wrap"><div id="board"></div></div>
  <div id="meta"></div>
<script>
const UNICODE = {
  wP: "\\u2659", wN: "\\u2658", wB: "\\u2657", wR: "\\u2656", wQ: "\\u2655", wK: "\\u2654",
  bP: "\\u265F", bN: "\\u265E", bB: "\\u265D", bR: "\\u265C", bQ: "\\u265B", bK: "\\u265A"
};
const TYPE_NAMES = { P: "pawn", N: "knight", B: "bishop", R: "rook", Q: "queen", K: "king" };

const boardWrapEl = document.getElementById("board-wrap");
const boardEl = document.getElementById("board");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const connEl = document.getElementById("conn");

// cellEls[r][c] = the .sq div. lastCodes[r][c] = piece code last painted
// there ("wN", etc.) or null. Built once; reused across every update so
// unchanged squares are never touched.
let cellEls = null;
let lastCodes = null;

// ---------------------------------------------------------------------
// Style state: persisted in localStorage so it's remembered per-browser
// across reloads, independent of whatever game is being watched.
// ---------------------------------------------------------------------
const LS_PREFIX = "chessViewer.";
function lsGet(key, fallback) {
  const v = localStorage.getItem(LS_PREFIX + key);
  return v === null ? fallback : v;
}
function lsSet(key, value) { localStorage.setItem(LS_PREFIX + key, value); }

let catalogue = { boards: { sets: [], darks: [], lights: [] }, pieces: { sets: [] } };

const style = {
  boardMode: lsGet("boardMode", "matched"),      // "matched" | "independent"
  boardSet: lsGet("boardSet", null),
  boardLight: lsGet("boardLight", null),
  boardDark: lsGet("boardDark", null),
  pieceMode: lsGet("pieceMode", "matched"),       // "matched" | "independent"
  pieceSet: lsGet("pieceSet", null),
  pieceWhite: lsGet("pieceWhite", null),
  pieceBlack: lsGet("pieceBlack", null),
};

function fillSelect(sel, options, selectedId) {
  sel.innerHTML = "";
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.id;
    el.textContent = opt.label;
    sel.appendChild(el);
  }
  if (selectedId && options.some(o => o.id === selectedId)) sel.value = selectedId;
  else if (options.length) sel.value = options[0].id;
}

function currentBoardTextures() {
  const { sets, darks, lights } = catalogue.boards;
  if (!sets.length) return { light: null, dark: null };
  if (style.boardMode === "matched") {
    const set = sets.find(s => s.id === style.boardSet) || sets[0];
    return { light: set.light, dark: set.dark };
  }
  const light = lights.find(l => l.id === style.boardLight) || lights[0];
  const dark = darks.find(d => d.id === style.boardDark) || darks[0];
  return { light, dark };
}

function currentPieceSets() {
  const { sets } = catalogue.pieces;
  if (!sets.length) return { white: null, black: null };
  if (style.pieceMode === "matched") {
    const set = sets.find(s => s.id === style.pieceSet) || sets[0];
    return { white: set, black: set };
  }
  const white = sets.find(s => s.id === style.pieceWhite) || sets[0];
  const black = sets.find(s => s.id === style.pieceBlack) || sets[0];
  return { white, black };
}

function applyBoardTextures() {
  const { light, dark } = currentBoardTextures();
  const root = document.documentElement.style;
  root.setProperty("--light-square-url", light ? `url("${light.url}")` : "none");
  root.setProperty("--dark-square-url", dark ? `url("${dark.url}")` : "none");
}

function pieceImageUrl(code) {
  // code like "wN" -> color "white"/"black" + type name
  const color = code[0] === "w" ? "white" : "black";
  const typeName = TYPE_NAMES[code[1]];
  const { white, black } = currentPieceSets();
  const set = color === "white" ? white : black;
  return set && set.pieces[color] ? set.pieces[color][typeName] : null;
}

// ---------------------------------------------------------------------
// Controls wiring
// ---------------------------------------------------------------------
function setupToggle(name, onChange) {
  const container = document.querySelector(`[data-toggle="${name}"]`);
  container.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      container.querySelectorAll("button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelector(`[data-panel="${name}-matched"]`).style.display = btn.dataset.mode === "matched" ? "" : "none";
      document.querySelector(`[data-panel="${name}-independent"]`).style.display = btn.dataset.mode === "independent" ? "flex" : "none";
      onChange(btn.dataset.mode);
    });
  });
}

function initControls() {
  const boardSetSel = document.getElementById("board-set");
  const boardLightSel = document.getElementById("board-light");
  const boardDarkSel = document.getElementById("board-dark");
  const pieceSetSel = document.getElementById("piece-set");
  const pieceWhiteSel = document.getElementById("piece-white");
  const pieceBlackSel = document.getElementById("piece-black");

  fillSelect(boardSetSel, catalogue.boards.sets, style.boardSet);
  fillSelect(boardLightSel, catalogue.boards.lights, style.boardLight);
  fillSelect(boardDarkSel, catalogue.boards.darks, style.boardDark);
  fillSelect(pieceSetSel, catalogue.pieces.sets, style.pieceSet);
  fillSelect(pieceWhiteSel, catalogue.pieces.sets, style.pieceWhite);
  fillSelect(pieceBlackSel, catalogue.pieces.sets, style.pieceBlack);

  style.boardSet = boardSetSel.value; lsSet("boardSet", style.boardSet);
  style.boardLight = boardLightSel.value; lsSet("boardLight", style.boardLight);
  style.boardDark = boardDarkSel.value; lsSet("boardDark", style.boardDark);
  style.pieceSet = pieceSetSel.value; lsSet("pieceSet", style.pieceSet);
  style.pieceWhite = pieceWhiteSel.value; lsSet("pieceWhite", style.pieceWhite);
  style.pieceBlack = pieceBlackSel.value; lsSet("pieceBlack", style.pieceBlack);

  document.querySelector(`[data-toggle="board"] [data-mode="${style.boardMode}"]`).click();
  document.querySelector(`[data-toggle="piece"] [data-mode="${style.pieceMode}"]`).click();

  setupToggle("board", mode => { style.boardMode = mode; lsSet("boardMode", mode); applyBoardTextures(); });
  setupToggle("piece", mode => { style.pieceMode = mode; lsSet("pieceMode", mode); repaintAllPieces(); });

  boardSetSel.addEventListener("change", () => { style.boardSet = boardSetSel.value; lsSet("boardSet", style.boardSet); applyBoardTextures(); });
  boardLightSel.addEventListener("change", () => { style.boardLight = boardLightSel.value; lsSet("boardLight", style.boardLight); applyBoardTextures(); });
  boardDarkSel.addEventListener("change", () => { style.boardDark = boardDarkSel.value; lsSet("boardDark", style.boardDark); applyBoardTextures(); });
  pieceSetSel.addEventListener("change", () => { style.pieceSet = pieceSetSel.value; lsSet("pieceSet", style.pieceSet); repaintAllPieces(); });
  pieceWhiteSel.addEventListener("change", () => { style.pieceWhite = pieceWhiteSel.value; lsSet("pieceWhite", style.pieceWhite); repaintAllPieces(); });
  pieceBlackSel.addEventListener("change", () => { style.pieceBlack = pieceBlackSel.value; lsSet("pieceBlack", style.pieceBlack); repaintAllPieces(); });

  applyBoardTextures();
}

function repaintAllPieces() {
  if (!cellEls || !lastCodes) return;
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      paintCell(cellEls[r][c], lastCodes[r][c]);
    }
  }
}

// ---------------------------------------------------------------------
// Board rendering
// ---------------------------------------------------------------------
function buildBoard() {
  boardEl.innerHTML = "";
  cellEls = [];
  lastCodes = [];
  for (let r = 0; r < 8; r++) {
    const rowEls = [];
    const rowCodes = [];
    for (let c = 0; c < 8; c++) {
      const sq = document.createElement("div");
      const light = (r + c) % 2 === 0;
      sq.className = "sq " + (light ? "light" : "dark");
      boardEl.appendChild(sq);
      rowEls.push(sq);
      rowCodes.push(undefined); // undefined = "never painted" (forces first paint)
    }
    cellEls.push(rowEls);
    lastCodes.push(rowCodes);
  }
}

function paintCell(sq, code) {
  sq.innerHTML = "";
  if (!code) return;
  const url = pieceImageUrl(code);
  if (!url) {
    const span = document.createElement("span");
    span.className = "piece glyph";
    span.textContent = UNICODE[code] || "";
    sq.appendChild(span);
    return;
  }
  const img = document.createElement("img");
  img.className = "piece";
  img.src = url;
  img.alt = code;
  img.onerror = function () {
    const span = document.createElement("span");
    span.className = "piece glyph";
    span.textContent = UNICODE[code] || "";
    sq.innerHTML = "";
    sq.appendChild(span);
  };
  sq.appendChild(img);
}

function render(state) {
  if (!state.started) {
    boardEl.innerHTML = "";
    cellEls = null;
    lastCodes = null;
    statusEl.className = "";
    statusEl.textContent = "No game in progress.";
    metaEl.textContent = "Start one via POST /api/game on port 5003.";
    return;
  }

  if (!cellEls) buildBoard();

  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const cell = state.board[r][c];
      const code = cell ? cell.code : null;
      if (lastCodes[r][c] !== code) {
        paintCell(cellEls[r][c], code);
        lastCodes[r][c] = code;
      }
    }
  }

  let text = (state.turn === "white" ? "White" : "Black") + " to move";
  statusEl.className = "";
  if (state.game_over) {
    text = "Game over \\u2014 " + state.status.replace(/_/g, " ");
    if (state.winner) text += " (" + state.winner + " wins)";
    statusEl.className = "over";
  } else if (state.in_check) {
    text += " \\u2014 check!";
  }
  statusEl.textContent = text;

  metaEl.textContent =
    "white: " + state.players.white + "  |  black: " + state.players.black +
    "  |  move " + state.fullmove_number;
}

function setConn(live) {
  connEl.className = live ? "live" : "down";
  connEl.textContent = live ? "live" : "reconnecting\\u2026";
}

// ---------------------------------------------------------------------
// Responsive sizing: recompute the largest square that fits the
// available space every time the wrapper resizes, so the board scales
// continuously with the window instead of jumping between breakpoints.
// ---------------------------------------------------------------------
function fitBoard() {
  const rect = boardWrapEl.getBoundingClientRect();
  const size = Math.max(160, Math.floor(Math.min(rect.width, rect.height)) - 8);
  boardEl.style.width = size + "px";
  boardEl.style.height = size + "px";
}
new ResizeObserver(fitBoard).observe(boardWrapEl);
window.addEventListener("resize", fitBoard);

// ---------------------------------------------------------------------
// Boot: load the style catalogue, wire up controls, then start the feed
// ---------------------------------------------------------------------
async function boot() {
  try {
    const res = await fetch("/api/catalogue");
    catalogue = await res.json();
  } catch (e) {
    // Catalogue is optional — the Unicode-glyph / flat-color fallback
    // still works fine without it.
  }
  initControls();
  fitBoard();
  startFeed();
}

function startFeed() {
  if (typeof EventSource === "undefined") {
    startPolling();
    return;
  }
  const source = new EventSource("/events");
  source.onopen = () => setConn(true);
  source.onmessage = (e) => {
    setConn(true);
    render(JSON.parse(e.data));
  };
  source.onerror = () => {
    // EventSource auto-reconnects; just reflect the outage in the UI.
    setConn(false);
  };
}

function startPolling() {
  // Fallback for browsers/proxies without SSE support: same render()
  // (still diffed, so still no flashing), just fetched on a timer.
  setConn(true);
  async function poll() {
    try {
      const res = await fetch("/state");
      render(await res.json());
      setConn(true);
    } catch (e) {
      setConn(false);
    } finally {
      setTimeout(poll, 1000);
    }
  }
  poll();
}

boot();
</script>
</body>
</html>
"""


def create_viewer_app(game):
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    @app.get("/")
    def index():
        return render_template_string(PAGE)

    @app.get("/api/catalogue")
    def catalogue():
        """Board-square and piece-set style options, for the on-page
        appearance controls. Purely presentational — has nothing to do
        with game state."""
        return jsonify(boards=load_board_catalogue(), pieces=load_piece_catalogue())

    @app.get("/state")
    def state():
        """One-off state fetch. Kept for the no-SSE fallback and for
        anyone who'd rather poll than stream."""
        if not game.is_started():
            return jsonify({"started": False})
        return jsonify(game.state())

    @app.get("/events")
    def events():
        """Server-Sent Events stream: pushes the current state once on
        connect, then again every time the game actually changes (move,
        new game, resignation, ...) — no fixed-interval polling."""

        def generate():
            version = -1  # guarantees the first wait_for_change() returns immediately
            while True:
                payload, version = game.wait_for_change(version, timeout=20)
                yield f"data: {json.dumps(payload)}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # in case this ever sits behind nginx
                "Connection": "keep-alive",
            },
        )

    return app
