"""Read-only web viewer (port 5004).

Shows the current board state only — no controls, no way to submit
moves. Polls its own `/state` endpoint (which reads the shared game
object directly, in-process — no HTTP hop to port 5003 needed) once a
second and re-renders.

Pieces are rendered as image tags pointing at /static/pieces/<code>.png
(e.g. wN.png for white knight, bK.png for black king — see
static/pieces/README.md). Until those images are supplied, each square
falls back to a Unicode chess glyph automatically via the <img> onerror
handler, so the viewer works out of the box.
"""

from flask import Flask, jsonify, render_template_string

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>GNU Chess &mdash; board viewer</title>
<style>
  :root { --light: #f0d9b5; --dark: #b58863; }
  * { box-sizing: border-box; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #1e1e1e; color: #eee;
    display: flex; flex-direction: column; align-items: center;
    padding: 2rem 1rem;
  }
  h1 { font-size: 1rem; font-weight: 500; color: #999; margin: 0 0 1rem; letter-spacing: 0.02em; }
  #status { margin-bottom: 1rem; font-size: 1.1rem; min-height: 1.4em; }
  #status.over { color: #e0b84f; font-weight: 600; }
  #board {
    display: grid;
    grid-template-columns: repeat(8, min(11vw, 64px));
    grid-template-rows: repeat(8, min(11vw, 64px));
    border: 4px solid #444;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }
  .sq { display: flex; align-items: center; justify-content: center; font-size: min(8vw, 2.4rem); user-select: none; }
  .sq.light { background: var(--light); }
  .sq.dark { background: var(--dark); }
  .sq img { width: 88%; height: 88%; object-fit: contain; }
  #meta { margin-top: 1rem; font-size: 0.85rem; color: #888; text-align: center; max-width: 520px; line-height: 1.6; }
  #placeholder { color: #777; font-size: 1rem; padding: 3rem 0; }
</style>
</head>
<body>
  <h1>GNU Chess &mdash; board viewer (read only)</h1>
  <div id="status">connecting&hellip;</div>
  <div id="board"></div>
  <div id="meta"></div>
<script>
const UNICODE = {
  wP: "\\u2659", wN: "\\u2658", wB: "\\u2657", wR: "\\u2656", wQ: "\\u2655", wK: "\\u2654",
  bP: "\\u265F", bN: "\\u265E", bB: "\\u265D", bR: "\\u265C", bQ: "\\u265B", bK: "\\u265A"
};

function render(state) {
  const boardEl = document.getElementById("board");
  const statusEl = document.getElementById("status");
  const metaEl = document.getElementById("meta");

  if (!state.started) {
    boardEl.innerHTML = "";
    statusEl.className = "";
    statusEl.textContent = "No game in progress.";
    metaEl.textContent = "Start one via POST /api/game on port 5003.";
    return;
  }

  boardEl.innerHTML = "";
  state.board.forEach((row, r) => {
    row.forEach((cell, c) => {
      const sq = document.createElement("div");
      const light = (r + c) % 2 === 0;
      sq.className = "sq " + (light ? "light" : "dark");
      if (cell) {
        const img = document.createElement("img");
        img.src = "/static/pieces/" + cell.code + ".png";
        img.alt = cell.code;
        img.onerror = function () {
          const span = document.createElement("span");
          span.textContent = UNICODE[cell.code] || "";
          this.replaceWith(span);
        };
        sq.appendChild(img);
      }
      boardEl.appendChild(sq);
    });
  });

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

async function poll() {
  try {
    const res = await fetch("/state");
    render(await res.json());
  } catch (e) {
    document.getElementById("status").textContent = "unable to reach game state";
  } finally {
    setTimeout(poll, 1000);
  }
}
poll();
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
        if not game.is_started():
            return jsonify({"started": False})
        return jsonify(game.state())

    return app
