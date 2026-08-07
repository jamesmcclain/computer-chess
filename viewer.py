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
"""

import json

from flask import Flask, Response, jsonify, render_template_string, stream_with_context

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>GNU Chess &mdash; board viewer</title>
<style>
  :root { --light: #f0d9b5; --dark: #b58863; --accent: #e0b84f; }
  * { box-sizing: border-box; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #1e1e1e; color: #eee;
    display: flex; flex-direction: column; align-items: center;
    padding: 2rem 1rem;
  }
  h1 { font-size: 1rem; font-weight: 500; color: #999; margin: 0 0 1rem; letter-spacing: 0.02em; }
  #conn { font-size: 0.75rem; color: #666; margin-bottom: 0.5rem; }
  #conn.live::before { content: "\\25CF"; color: #4caf50; margin-right: 0.4em; }
  #conn.down::before { content: "\\25CF"; color: #b04a4a; margin-right: 0.4em; }
  #status { margin-bottom: 1rem; font-size: 1.1rem; min-height: 1.4em; transition: color 0.3s ease; }
  #status.over { color: var(--accent); font-weight: 600; }
  #board {
    position: relative;
    display: grid;
    grid-template-columns: repeat(8, min(11vw, 64px));
    grid-template-rows: repeat(8, min(11vw, 64px));
    border: 4px solid #444;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }
  .sq { display: flex; align-items: center; justify-content: center; font-size: min(8vw, 2.4rem); user-select: none; position: relative; }
  .sq.light { background: var(--light); }
  .sq.dark { background: var(--dark); }
  .sq .piece { width: 88%; height: 88%; object-fit: contain; animation: pop-in 0.18s ease-out; }
  .sq .piece.glyph { font-size: min(8vw, 2.4rem); line-height: 1; animation: pop-in 0.18s ease-out; }
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
  <div id="board"></div>
  <div id="meta"></div>
<script>
const UNICODE = {
  wP: "\\u2659", wN: "\\u2658", wB: "\\u2657", wR: "\\u2656", wQ: "\\u2655", wK: "\\u2654",
  bP: "\\u265F", bN: "\\u265E", bB: "\\u265D", bR: "\\u265C", bQ: "\\u265B", bK: "\\u265A"
};

const boardEl = document.getElementById("board");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const connEl = document.getElementById("conn");

// cellEls[r][c] = the .sq div. lastCodes[r][c] = piece code last painted
// there ("wN", etc.) or null. Built once; reused across every update so
// unchanged squares are never touched.
let cellEls = null;
let lastCodes = null;

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
  const img = document.createElement("img");
  img.className = "piece";
  img.src = "/static/pieces/" + code + ".png";
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

if (typeof EventSource === "undefined") {
  startPolling();
} else {
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
</script>
</body>
</html>
"""


def create_viewer_app(game):
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    @app.get("/")
    def index():
        return render_template_string(PAGE)

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
