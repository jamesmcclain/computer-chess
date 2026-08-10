"""Web viewer (port 5004).

Shows the current board live over Server-Sent Events (SSE) rather than
polled on a fixed interval: the server blocks (via
`ChessGame.wait_for_change`) until the game actually changes, then
streams the new state down an open `/events` connection. The client only
rewrites the board squares that actually changed piece, instead of
tearing down and rebuilding the whole board on every tick — that
per-square-teardown was the source of the old polling viewer's
"flashing" (recreated <img> tags briefly show a broken-image icon before
their onerror fallback kicks in, even when nothing on that square
changed).

SSE was chosen over WebSockets because the live-board feed is one-way,
server-to-client (`/events`) — a plain HTTP streaming response covers
that with no extra protocol, dependency, or handshake, and works with
Flask's built-in dev server out of the box.

This page is not purely read-only: when no game is in progress
(including after a previous one has finished), it offers a form to
start one, letting a person pick each side's type — 'api-user' (an
outside caller, e.g. an agent), 'engine' (GNU Chess or Stockfish), or
'web-user' (play by clicking this page) — plus, if either side is
'engine', which engine plays it and its difficulty. While a game is
running, if it is a 'web-user' side's turn, this page also lets a
person click a piece and a destination square to submit that side's
move.

The `/game/*` routes below exist only so this page's own JS can start
games and submit moves same-origin, without depending on the REST API's
host/port (which can be remapped independently — see run.sh). They are
thin wrappers over the same shared `ChessGame` object the REST API
(port 5003, api.py) uses, so behavior and validation are identical
either way; the REST API remains the one to use for programmatic play
(see SKILL.md).

Appearance (board squares + piece art) is a purely client-side, per-
browser preference: it's read from the theme catalogue below, picked
with on-page controls, and remembered in the browser's localStorage.
It never touches game state, so different people watching the same
game can each see their own board/piece styles.
"""

import json
import os

from flask import Flask, Response, jsonify, render_template_string, request, stream_with_context

from game import EVAL_QUALITIES, GameError, describe_eval_qualities, describe_levels

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
<title>computer-chess &mdash; board viewer</title>
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

  /* ---- start-a-game panel ------------------------------------------------
     Shown whenever no game is in progress (never started, or the last
     one finished) — see needsStart() in the JS below. */
  #start-panel {
    display: none;
    flex-direction: column; gap: 0.6rem; align-items: stretch;
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 1rem;
    width: 100%; max-width: 360px;
  }
  #start-panel h2 { margin: 0 0 0.2rem; font-size: 0.95rem; font-weight: 600; color: #ddd; }
  .start-row { display: flex; align-items: center; gap: 0.6rem; }
  .start-row label { color: #aaa; font-size: 0.82rem; width: 6rem; flex-shrink: 0; }
  .start-row select { font-size: 0.85rem; }
  .friend-inputs { display: flex; flex-direction: column; gap: 0.5rem; width: 100%; }
  .friend-engine-row { display: flex; flex-direction: column; gap: 0.25rem; }
  .friend-engine-row .friend-inputs-tag.friend-engine-name {
    color: #ccc; font-size: 0.78rem; font-weight: 600;
  }
  .friend-engine-pairs { display: flex; align-items: center; gap: 0.7rem; flex-wrap: wrap; }
  .friend-inputs-pair { display: flex; align-items: center; gap: 0.3rem; white-space: nowrap; }
  .friend-inputs input[type="number"] {
    background: #1a1a1a; color: #eee; border: 1px solid #444; border-radius: 6px;
    padding: 0.25rem 0.4rem; font-size: 0.8rem; width: 3.2rem;
  }
  .friend-inputs input[type="number"]:focus { outline: 1px solid var(--accent); }
  .friend-inputs-tag { color: #888; font-size: 0.72rem; }
  #start-btn {
    all: unset; cursor: pointer; text-align: center; margin-top: 0.3rem;
    background: var(--accent); color: #1a1a1a; font-weight: 600; font-size: 0.85rem;
    padding: 0.5rem; border-radius: 6px;
  }
  #start-btn:hover { filter: brightness(1.08); }
  #start-error { color: #e07a7a; font-size: 0.78rem; min-height: 1em; }

  /* ---- click-to-move -------------------------------------------------- */
  #board.interactive .sq.movable { cursor: pointer; }
  #board.interactive .sq.movable:hover { filter: brightness(1.12); }
  .sq.selected { box-shadow: inset 0 0 0 3px var(--accent); }
  .sq.legal-target { cursor: pointer; }
  .sq.legal-target::after {
    content: ""; position: absolute; width: 26%; height: 26%; border-radius: 50%;
    background: rgba(224, 184, 79, 0.85); pointer-events: none;
  }
  .sq.legal-target.has-piece::after {
    width: 92%; height: 92%; border-radius: 50%; background: transparent;
    box-shadow: inset 0 0 0 4px rgba(224, 184, 79, 0.85);
  }
  #promo-picker {
    display: none; position: absolute; z-index: 10;
    background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 0.3rem; box-shadow: 0 6px 20px rgba(0,0,0,0.5);
    gap: 0.25rem;
  }
  #promo-picker button {
    all: unset; cursor: pointer; width: 2.2rem; height: 2.2rem; display: flex;
    align-items: center; justify-content: center; border-radius: 6px;
    background: #1a1a1a; font-size: 1.3rem;
  }
  #promo-picker button:hover { background: var(--accent); color: #1a1a1a; }

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
  /* align-items: stretch (the default) is required here, not center —
     #board-wrap's own "flex: 1 1 auto" only grows it to fill #board-row's
     height if #board-row lets it stretch that far; align-items: center
     would instead size #board-wrap to its (initially empty) content, and
     since fitBoard() measures #board-wrap's own height to size the board,
     that shrinks the whole board down to its 160px floor. */
  #board-row { display: flex; justify-content: center; gap: 0.7rem; width: 100%; flex: 1 1 auto; min-height: 0; }
  #board-wrap { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; width: 100%; min-height: 0; }

  /* ---- eval bar -----------------------------------------------------
     A vertical bar showing the eval-bar engine's live Stockfish
     assessment of the position, White's share on top (light) growing
     down over Black's share (dark) — mirrors how a chess.com/lichess
     eval bar reads. Sized in JS (fitBoard) to match the board's own
     height, since #board is sized there too. */
  #eval-bar-wrap { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.3rem; flex: 0 0 auto; }
  #eval-bar {
    width: 1.1rem; border-radius: 4px; overflow: hidden; background: #1a1a1a;
    border: 1px solid var(--panel-border); display: flex; flex-direction: column;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
  }
  #eval-bar-fill {
    width: 100%; background: #f0d9b5; margin-top: auto; /* grows up from the bottom = Black's share */
    transition: height 0.4s ease, background 0.4s ease;
  }
  #eval-bar-label {
    font-size: 0.68rem; color: #999; font-variant-numeric: tabular-nums;
    min-width: 2.6em; text-align: center;
  }
  #eval-quality-desc { color: #888; font-size: 0.68rem; line-height: 1.35; min-height: 2.6em; }
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

  /* ---- players bar -------------------------------------------------------
     A compact "White vs Black" header above the board, showing each side's
     display name (if set — see set_name()), type, and engine level. The
     side currently on the move gets a highlighted pill so it's obvious at
     a glance whose turn it is, without having to read the status line. */
  #players-bar {
    display: none; align-items: center; justify-content: center; gap: 0.6rem;
    margin-bottom: 0.75rem; font-size: 0.85rem;
  }
  #players-bar .side {
    display: flex; align-items: center; gap: 0.4rem; padding: 0.3rem 0.7rem;
    border-radius: 999px; background: var(--panel-bg); border: 1px solid var(--panel-border);
    color: #bbb; transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
  }
  #players-bar .side .dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; flex-shrink: 0; }
  #players-bar .side.white .dot { background: #f0d9b5; }
  #players-bar .side.black .dot { background: #b58863; }
  #players-bar .side.to-move { background: var(--accent); border-color: var(--accent); color: #1a1a1a; font-weight: 600; }
  #players-bar .vs { color: #666; font-size: 0.75rem; }

  /* ---- resign / restart --------------------------------------------------
     One button, shown only while a game is in progress. Its label and
     effect depend on whether a person could be behind either side (see
     updateGameControls()): "Resign" (a real move, ends the game — see
     POST /game/resign) if at least one side is 'web-user', else
     "Restart" (just reveals the start form below early, so a spectator
     of an api-user/engine game — including an engine-vs-engine one —
     isn't stuck waiting for it to finish on its own). */
  #game-control-btn {
    display: none; all: unset; cursor: pointer; margin-bottom: 0.75rem;
    font-size: 0.75rem; padding: 0.3rem 0.8rem; border-radius: 999px;
    background: #1a1a1a; border: 1px solid #5a3a3a; color: #d99;
  }
  #game-control-btn:hover { background: #2a1a1a; }
  #resign-both-row { display: none; gap: 0.5rem; margin-bottom: 0.75rem; }
  .resign-side-btn {
    all: unset; cursor: pointer; font-size: 0.75rem; padding: 0.3rem 0.8rem;
    border-radius: 999px; background: #1a1a1a; border: 1px solid #5a3a3a; color: #d99;
  }
  .resign-side-btn:hover { background: #2a1a1a; }

  /* ---- last-move arrow ----------------------------------------------------
     A semi-transparent arrow drawn from a moved piece's old square to its
     new one — for a move by any side (web user, API user, or engine
     alike) — so the move that just happened is obvious even to someone
     glancing at the board mid-game rather than watching it live. Uses an
     SVG with viewBox="0 0 8 8" (one unit per square) laid over the board,
     so it scales with the board automatically — no JS resize math needed,
     unlike the pixel-based board sizing above. Fades out on its own after
     ARROW_FADE_MS if no further move arrives — see updateMoveArrow(). */
  #move-arrow { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; overflow: visible; }
  #move-arrow-line { fill: none; stroke: var(--accent); stroke-width: 0.14; stroke-linecap: round; opacity: 0; transition: opacity 0.5s ease; }
  #move-arrow-line.shown { opacity: 0.85; }
  #move-arrow-head { fill: var(--accent); opacity: 0; transition: opacity 0.5s ease; }
  #move-arrow-head.shown { opacity: 0.85; }

  /* ---- transcript button -------------------------------------------------
     Shown only while state.game_over is true (and hidden again the moment
     a new game actually starts, since that flips game_over back to
     false) — see updateTranscriptButton(). Downloads the just-finished
     game as a PGN file straight from GET /game/transcript; no JS beyond
     toggling visibility is needed since that's a plain same-origin GET
     with a Content-Disposition header. */
  #transcript-btn {
    display: none; text-decoration: none; text-align: center;
    background: var(--panel-bg); border: 1px solid var(--panel-border); color: #ddd;
    font-size: 0.82rem; font-weight: 600; padding: 0.5rem 1rem; border-radius: 8px;
    margin-bottom: 0.75rem;
  }
  #transcript-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ---- chat -------------------------------------------------------------
     All chat rides along with a move (POST /api/game/move's 'chat'
     field, shown next to that move's SAN in the panel below) — there is
     no standalone/banter channel. A person at this page can type a
     message here while it's their turn; it's attached automatically to
     whichever move they submit next (see submitMove()) — shown only
     when it's currently a 'web-user' side's own turn (see
     updateChatInput()), since there's no other way for chat to go out. */
  #chat-panel {
    display: none; flex-direction: column; width: 100%; max-width: 420px;
    margin-top: 1rem; background: var(--panel-bg); border: 1px solid var(--panel-border);
    border-radius: 10px; overflow: hidden;
  }
  #chat-panel .chat-title {
    padding: 0.5rem 0.8rem; font-size: 0.68rem; font-weight: 600; letter-spacing: 0.02em;
    text-transform: uppercase; color: #aaa; border-bottom: 1px solid var(--panel-border);
  }
  #chat-log { display: flex; flex-direction: column; gap: 0.5rem; padding: 0.6rem 0.8rem; max-height: 10rem; overflow-y: auto; }
  #chat-log .chat-line { font-size: 0.8rem; line-height: 1.4; color: #ddd; }
  #chat-log .chat-line .chat-name { color: var(--accent); font-weight: 600; }
  #chat-log .chat-line .chat-move { color: #777; font-size: 0.72rem; }
  #chat-input-row {
    display: none; gap: 0.4rem; padding: 0.5rem 0.6rem; border-top: 1px solid var(--panel-border);
  }
  #chat-input-row input {
    flex: 1 1 auto; min-width: 0; background: #1a1a1a; color: #eee; border: 1px solid #444;
    border-radius: 6px; padding: 0.35rem 0.5rem; font-size: 0.8rem;
  }
  #chat-input-row input:focus { outline: 1px solid var(--accent); }
</style>
</head>
<body>
  <h1>computer-chess &mdash; board viewer</h1>
  <div id="conn">connecting&hellip;</div>
  <div id="status"></div>

  <a id="transcript-btn" href="/game/transcript">Download transcript (PGN)</a>

  <div id="start-panel">
    <h2>Start a new game</h2>
    <div class="start-row"><label>White</label><select id="start-white"></select></div>
    <div class="start-row" id="start-white-engine-row" style="display:none;">
      <label>White engine</label><select id="start-white-engine"></select>
    </div>
    <div class="start-row" id="start-white-level-row" style="display:none;">
      <label>White level</label><select id="start-white-level"></select>
    </div>
    <div class="start-row"><label>Black</label><select id="start-black"></select></div>
    <div class="start-row" id="start-black-engine-row" style="display:none;">
      <label>Black engine</label><select id="start-black-engine"></select>
    </div>
    <div class="start-row" id="start-black-level-row" style="display:none;">
      <label>Black level</label><select id="start-black-level"></select>
    </div>
    <div class="start-row" id="start-friend-row" style="display:none;">
      <label>Friend calls</label>
      <span class="friend-inputs" id="start-friend-engines"></span>
    </div>
    <button id="start-btn">Start game</button>
    <div id="start-error"></div>
  </div>

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

    <div class="ctrl-group">
      <div class="ctrl-title">Eval bar</div>
      <select id="eval-quality-sel"></select>
      <div id="eval-quality-desc"></div>
    </div>
  </div>

  <div id="players-bar">
    <div class="side white" id="side-white"><span class="dot"></span><span class="side-label"></span></div>
    <span class="vs">vs</span>
    <div class="side black" id="side-black"><span class="dot"></span><span class="side-label"></span></div>
  </div>

  <button id="game-control-btn"></button>
  <span id="resign-both-row" style="display:none;">
    <button id="resign-white-btn" class="resign-side-btn">Resign white</button>
    <button id="resign-black-btn" class="resign-side-btn">Resign black</button>
  </span>

  <div id="board-row">
    <div id="eval-bar-wrap" title="">
      <div id="eval-bar"><div id="eval-bar-fill"></div></div>
      <div id="eval-bar-label"></div>
    </div>
    <div id="board-wrap"><div id="board"></div><div id="promo-picker"></div></div>
  </div>
  <div id="meta"></div>
  <div id="chat-panel">
    <div class="chat-title">Chat</div>
    <div id="chat-log"></div>
    <div id="chat-input-row">
      <input id="chat-input" type="text" maxlength="240" placeholder="Message to send with your next move&hellip;">
    </div>
  </div>
<script>
const UNICODE = {
  wP: "\\u2659", wN: "\\u2658", wB: "\\u2657", wR: "\\u2656", wQ: "\\u2655", wK: "\\u2654",
  bP: "\\u265F", bN: "\\u265E", bB: "\\u265D", bR: "\\u265C", bQ: "\\u265B", bK: "\\u265A"
};
const TYPE_NAMES = { P: "pawn", N: "knight", B: "bishop", R: "rook", Q: "queen", K: "king" };
const FILES = "abcdefgh";

const PLAYER_TYPES = [
  { id: "api-user", label: "API user" },
  { id: "api-trainee", label: "API trainee" },
  { id: "engine", label: "Engine" },
  { id: "web-user", label: "Web user (you)" },
];

const ENGINE_TYPES = [
  { id: "gnuchess", label: "GNU Chess" },
  { id: "stockfish", label: "Stockfish" },
];

// Mirrors FRIEND_EVAL_KEY in game.py: the key the "eval" phone-a-friend
// kind's budget appears under in state.phone_a_friend, beside the
// per-engine move-hint budgets. Not an entry in ENGINE_TYPES above — no
// side can be played by it; it only ever answers "who is winning?".
const FRIEND_EVAL_KEY = "stockfish_eval";

const boardWrapEl = document.getElementById("board-wrap");
const boardEl = document.getElementById("board");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const connEl = document.getElementById("conn");
const startPanelEl = document.getElementById("start-panel");
const promoPickerEl = document.getElementById("promo-picker");
const playersBarEl = document.getElementById("players-bar");
const sideWhiteEl = document.getElementById("side-white");
const sideBlackEl = document.getElementById("side-black");
const chatPanelEl = document.getElementById("chat-panel");
const chatLogEl = document.getElementById("chat-log");
const chatInputRowEl = document.getElementById("chat-input-row");
const chatInputEl = document.getElementById("chat-input");
const gameControlBtnEl = document.getElementById("game-control-btn");
const resignBothRowEl = document.getElementById("resign-both-row");
const resignWhiteBtnEl = document.getElementById("resign-white-btn");
const resignBlackBtnEl = document.getElementById("resign-black-btn");
const transcriptBtnEl = document.getElementById("transcript-btn");
const evalBarWrapEl = document.getElementById("eval-bar-wrap");
const evalBarEl = document.getElementById("eval-bar");
const evalBarFillEl = document.getElementById("eval-bar-fill");
const evalBarLabelEl = document.getElementById("eval-bar-label");
const evalQualitySelEl = document.getElementById("eval-quality-sel");
const evalQualityDescEl = document.getElementById("eval-quality-desc");

let evalQualities = []; // [{id, label, description}, ...], loaded from /game/eval-qualities

// cellEls[r][c] = the .sq div. lastCodes[r][c] = piece code last painted
// there ("wN", etc.) or null. Built once; reused across every update so
// unchanged squares are never touched.
let cellEls = null;
let lastCodes = null;
let latestState = null; // most recent state from render(), used by click-to-move

// Click-to-move state: the square a person just clicked (algebraic, e.g.
// "e2"), and a map of "to square" -> [legal move, ...] for it (more than
// one entry only happens for a promotion, where several pieces are
// offered for the same destination square).
let selectedSquare = null;
let legalTargets = {};

// Last-move arrow: line + arrowhead elements (built once in buildBoard(),
// since it clears #board's contents), how many move_log entries were on
// the board the last time the arrow was drawn — a length change means
// there's a new last move to point at (see updateMoveArrow()) — and the
// pending fade-out timer, restarted on every new move so the arrow always
// shows for a full ARROW_FADE_MS after the *latest* move, not the first.
let moveArrowLineEl = null;
let moveArrowHeadEl = null;
let lastArrowLogLength = 0;
let arrowFadeTimer = null;
const ARROW_FADE_MS = 60000;

// Chat: how many move_log entries have already been rendered into the
// chat panel, so re-renders only add the entries not yet shown — see
// updateChatPanel().
let lastChatMoveLogLength = 0;

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
  boardSet: lsGet("boardSet", "mahogany_ash"),
  boardLight: lsGet("boardLight", "ash"),
  boardDark: lsGet("boardDark", "mahogany"),
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
const SVG_NS = "http://www.w3.org/2000/svg";

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
      sq.dataset.square = squareName(r, c);
      sq.addEventListener("click", () => onSquareClick(r, c));
      boardEl.appendChild(sq);
      rowEls.push(sq);
      rowCodes.push(undefined); // undefined = "never painted" (forces first paint)
    }
    cellEls.push(rowEls);
    lastCodes.push(rowCodes);
  }

  // Last-move arrow overlay: one unit per square (viewBox "0 0 8 8"), so
  // it scales with the board with no JS resize math of its own — see the
  // #move-arrow CSS comment. Rebuilt here since boardEl.innerHTML was
  // just cleared above.
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("id", "move-arrow");
  svg.setAttribute("viewBox", "0 0 8 8");
  moveArrowLineEl = document.createElementNS(SVG_NS, "line");
  moveArrowLineEl.setAttribute("id", "move-arrow-line");
  moveArrowHeadEl = document.createElementNS(SVG_NS, "polygon");
  moveArrowHeadEl.setAttribute("id", "move-arrow-head");
  svg.appendChild(moveArrowLineEl);
  svg.appendChild(moveArrowHeadEl);
  boardEl.appendChild(svg);
  lastArrowLogLength = 0;
  if (arrowFadeTimer) { clearTimeout(arrowFadeTimer); arrowFadeTimer = null; }
}

// ---------------------------------------------------------------------
// Last-move arrow: draws a semi-transparent arrow from the previous move's
// source square to its destination, so the move that just happened reads
// clearly at a glance (not just from the piece having "popped in" at its
// new square). Only redraws when move_log actually grew, so it doesn't
// re-animate on unrelated state pushes (e.g. someone else's chat message).
// ---------------------------------------------------------------------
function updateMoveArrow(state) {
  const log = (state && state.move_log) || [];
  if (!moveArrowLineEl) return;
  if (!state || !state.started || log.length === 0) {
    hideMoveArrow();
    lastArrowLogLength = 0;
    return;
  }
  if (log.length === lastArrowLogLength) return;
  lastArrowLogLength = log.length;

  const last = log[log.length - 1];
  if (!last.uci || last.uci.length < 4) {
    hideMoveArrow();
    return;
  }
  const [fr, fc] = squareToRC(last.uci.slice(0, 2));
  const [tr, tc] = squareToRC(last.uci.slice(2, 4));
  // Drawn the same way regardless of who made the move — 'by' is not
  // checked here — so an engine's move or another API user's move gets
  // the same clear arrow as one made by a person clicking the board.
  drawMoveArrow(fc + 0.5, fr + 0.5, tc + 0.5, tr + 0.5);
}

function hideMoveArrow() {
  moveArrowLineEl.classList.remove("shown");
  moveArrowHeadEl.classList.remove("shown");
  if (arrowFadeTimer) { clearTimeout(arrowFadeTimer); arrowFadeTimer = null; }
}

function drawMoveArrow(x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist, uy = dy / dist;

  // Inset both ends a bit so the line starts clear of the origin piece
  // and stops short of the arrowhead rather than running through it.
  const startInset = 0.15, endInset = 0.42;
  moveArrowLineEl.setAttribute("x1", x1 + ux * startInset);
  moveArrowLineEl.setAttribute("y1", y1 + uy * startInset);
  moveArrowLineEl.setAttribute("x2", x2 - ux * endInset);
  moveArrowLineEl.setAttribute("y2", y2 - uy * endInset);

  const headLen = 0.32, headWidth = 0.22;
  const tipX = x2 - ux * 0.08, tipY = y2 - uy * 0.08;
  const baseX = tipX - ux * headLen, baseY = tipY - uy * headLen;
  const px = -uy, py = ux; // perpendicular unit vector, for the arrowhead's base corners
  const p1x = baseX + px * headWidth, p1y = baseY + py * headWidth;
  const p2x = baseX - px * headWidth, p2y = baseY - py * headWidth;
  moveArrowHeadEl.setAttribute("points", `${tipX},${tipY} ${p1x},${p1y} ${p2x},${p2y}`);

  moveArrowLineEl.classList.add("shown");
  moveArrowHeadEl.classList.add("shown");

  // Fade the arrow out if this stays the latest move for a full minute —
  // restarted on every call, so a flurry of moves keeps it visible for
  // ARROW_FADE_MS after the *last* one, not the first.
  if (arrowFadeTimer) clearTimeout(arrowFadeTimer);
  arrowFadeTimer = setTimeout(() => {
    moveArrowLineEl.classList.remove("shown");
    moveArrowHeadEl.classList.remove("shown");
    arrowFadeTimer = null;
  }, ARROW_FADE_MS);
}

// ---------------------------------------------------------------------
// Click-to-move (for a "web-user" side's turn only — an "api-user" or
// "engine" turn ignores clicks; nothing here changes who can call the
// REST API directly, this only gates this page's own UI affordance).
// ---------------------------------------------------------------------
function squareName(r, c) {
  // row 0 = rank 8 (see game.py's _board_grid), column 0 = file a.
  return FILES[c] + String(8 - r);
}

function squareToRC(square) {
  const file = square[0];
  const rank = parseInt(square.slice(1), 10);
  return [8 - rank, FILES.indexOf(file)];
}

function myTurnIsWebUser(state) {
  return !!(state && state.started && !state.game_over && state.players[state.turn] === "web-user");
}

function clearSelection() {
  selectedSquare = null;
  legalTargets = {};
  if (cellEls) {
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        cellEls[r][c].classList.remove("selected", "legal-target", "has-piece");
      }
    }
  }
  hidePromotionPicker();
}

function updateInteractivity(state) {
  boardEl.classList.toggle("interactive", myTurnIsWebUser(state));
  if (!cellEls) return;
  const canMove = myTurnIsWebUser(state);
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      const cell = state.started ? state.board[r][c] : null;
      const movable = canMove && cell && cell.color === state.turn;
      cellEls[r][c].classList.toggle("movable", !!movable);
    }
  }
}

async function onSquareClick(r, c) {
  if (!myTurnIsWebUser(latestState)) return;
  const sq = squareName(r, c);
  const cell = latestState.board[r][c];
  const sideToMove = latestState.turn;

  if (selectedSquare && legalTargets[sq]) {
    const moves = legalTargets[sq];
    const chosen = moves.length === 1 ? moves[0] : null;
    clearSelection();
    if (chosen) {
      await submitMove(chosen.uci);
    } else {
      showPromotionPicker(r, c, moves);
    }
    return;
  }

  clearSelection();
  if (cell && cell.color === sideToMove) {
    selectedSquare = sq;
    cellEls[r][c].classList.add("selected");
    try {
      const res = await fetch(`/game/legal-moves?from=${encodeURIComponent(sq)}`);
      const data = await res.json();
      for (const m of data.moves || []) {
        (legalTargets[m.to] = legalTargets[m.to] || []).push(m);
      }
      for (const toSquare of Object.keys(legalTargets)) {
        const target = document.querySelector(`.sq[data-square="${toSquare}"]`);
        if (!target) continue;
        target.classList.add("legal-target");
        const [tr, tc] = squareToRC(toSquare);
        if (latestState.board[tr][tc]) target.classList.add("has-piece");
      }
    } catch (e) {
      clearSelection();
    }
  }
}

async function submitMove(uci) {
  // If the chat input is showing and has text, it rides along with this
  // move — there's no standalone send (see updateChatInput()).
  const chatText = chatInputRowEl.style.display !== "none" ? chatInputEl.value.trim() : "";
  try {
    const body = { move: uci };
    if (chatText) body.chat = chatText;
    const res = await fetch("/game/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      statusEl.textContent = data.error || "That move was rejected.";
    } else {
      chatInputEl.value = ""; // sent along with the move that just went out
    }
    // On success, the new state arrives through the SSE stream — no
    // need to render it here too.
  } catch (e) {
    statusEl.textContent = "Could not reach the server to submit that move.";
  }
}

function hidePromotionPicker() {
  promoPickerEl.style.display = "none";
  promoPickerEl.innerHTML = "";
}

const PROMO_GLYPH = { Q: "\\u265B", R: "\\u265C", B: "\\u265D", N: "\\u265E" };

function showPromotionPicker(r, c, moves) {
  promoPickerEl.innerHTML = "";
  for (const move of moves) {
    const btn = document.createElement("button");
    btn.textContent = PROMO_GLYPH[move.promotion] || move.promotion || "?";
    btn.title = move.san;
    btn.addEventListener("click", () => {
      hidePromotionPicker();
      submitMove(move.uci);
    });
    promoPickerEl.appendChild(btn);
  }
  const sqEl = cellEls[r][c];
  const wrapRect = boardWrapEl.getBoundingClientRect();
  const sqRect = sqEl.getBoundingClientRect();
  promoPickerEl.style.left = (sqRect.left - wrapRect.left) + "px";
  promoPickerEl.style.top = (sqRect.top - wrapRect.top - 48) + "px";
  promoPickerEl.style.display = "flex";
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
  latestState = state;
  clearSelection(); // any server-pushed state invalidates a local selection

  // Offer the start-game form whenever there's no game to watch — never
  // started, or the last one already finished (including a Restart —
  // see doRestart(), which ends the running game outright via
  // POST /game/abort before this ever needs to distinguish "finished"
  // from "still going").
  const needsStart = !state.started || state.game_over;
  startPanelEl.style.display = needsStart ? "flex" : "none";
  // Disappears again the moment a new game starts, since that flips
  // game_over back to false.
  transcriptBtnEl.style.display = (state.started && state.game_over) ? "inline-block" : "none";

  if (!state.started) {
    boardEl.innerHTML = "";
    cellEls = null;
    lastCodes = null;
    moveArrowLineEl = null;
    moveArrowHeadEl = null;
    boardWrapEl.style.display = "none";
    evalBarWrapEl.style.display = "none";
    playersBarEl.style.display = "none";
    gameControlBtnEl.style.display = "none";
    chatPanelEl.style.display = "none";
    chatInputRowEl.style.display = "none";
    chatLogEl.innerHTML = "";
    lastChatMoveLogLength = 0;
    statusEl.className = "";
    statusEl.textContent = "No game in progress. Start one below.";
    metaEl.textContent = "";
    return;
  }

  boardWrapEl.style.display = "flex";
  if (!cellEls) buildBoard();
  updateEvalBar(state);

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
  updateInteractivity(state);
  updateMoveArrow(state);
  updatePlayersBar(state);
  updateGameControls(state);
  updateChatPanel(state);
  updateChatInput(state);

  let text = (state.turn === "white" ? "White" : "Black") + " to move";
  statusEl.className = "";
  if (state.game_over) {
    text = "Game over \\u2014 " + state.status.replace(/_/g, " ");
    if (state.winner) text += " (" + state.winner + " wins)";
    text += ". Start a new game below to keep playing.";
    statusEl.className = "over";
  } else if (state.in_check) {
    text += " \\u2014 check!";
  } else if (myTurnIsWebUser(state)) {
    text += " \\u2014 your move: click a piece, then a highlighted square";
  }
  statusEl.textContent = text;

  metaEl.textContent =
    "white: " + sideLabel(state, "white") + "  |  black: " + sideLabel(state, "black") +
    "  |  move " + state.fullmove_number;
}

// Text form of a side's identity, shared by the meta line and the players
// bar: its display name if one is set (see set_name()), its type, and —
// for an "engine" side — its difficulty level.
function sideLabel(state, color) {
  const type = state.players[color];
  const name = state.player_names && state.player_names[color];
  let typeLabel = type;
  if (type === "engine" && state.engine_levels) {
    const engineName = state.engine_names && state.engine_names[color];
    const engineLabel = (ENGINE_TYPES.find(e => e.id === engineName) || {}).label || engineName || type;
    typeLabel = engineLabel + " (level " + state.engine_levels[color] + ")";
  } else if ((type === "api-user" || type === "api-trainee") && state.phone_a_friend) {
    const f = state.phone_a_friend[color];
    if (f) {
      // Each engine has its own independent quota (see phone_a_friend()
      // in game.py) — show both, e.g. "GNU Chess 1/1 L20, Stockfish inf L20".
      // A limit of -1 (FRIEND_LIMIT_UNLIMITED) means that tier has no cap.
      const fmtTier = (remaining, limit) => limit === -1 ? "inf" : (remaining + "/" + limit);
      const perEngine = ENGINE_TYPES.map(e => {
        const eng = f[e.id];
        const limits = state.phone_a_friend.limits[e.id];
        if (!eng || !limits) return null;
        return e.label + " " + fmtTier(eng.remaining.level_20, limits.level_20) + " L20, " +
          fmtTier(eng.remaining.level_10, limits.level_10) + " L10";
      }).filter(Boolean).concat(
        // The "eval" kind's own budget (FRIEND_EVAL_KEY in game.py) sits
        // beside the per-engine entries in the same used/remaining shape,
        // but has a single tier rather than L20/L10.
        (() => {
          const ev = f[FRIEND_EVAL_KEY];
          const evLimits = state.phone_a_friend.limits[FRIEND_EVAL_KEY];
          if (!ev || !evLimits) return [];
          return ["Stockfish eval " + fmtTier(ev.remaining.eval, evLimits.eval)];
        })()
      ).join("; ");
      typeLabel = type + (perEngine ? " (friend: " + perEngine + ")" : "");
    }
  }
  return name ? name + " \\u2014 " + typeLabel : typeLabel;
}

function updatePlayersBar(state) {
  playersBarEl.style.display = "flex";
  sideWhiteEl.querySelector(".side-label").textContent = sideLabel(state, "white");
  sideBlackEl.querySelector(".side-label").textContent = sideLabel(state, "black");
  const toMove = !state.game_over ? state.turn : null;
  sideWhiteEl.classList.toggle("to-move", toMove === "white");
  sideBlackEl.classList.toggle("to-move", toMove === "black");
}

// ---------------------------------------------------------------------
// Chat: a short line an API user (or a web user — see below) attached
// to a move (the 'chat' field on POST /api/game/move, stamped onto that
// move's move_log entry). There is no standalone/banter channel — every
// line shown here belongs to a specific move. Not polled for — it
// arrives as part of the normal state push (SSE or otherwise); this
// just renders whichever entries carry a 'chat'. Only the entries not
// yet shown are appended, and the panel only auto-scrolls if it was
// already scrolled to the bottom, so it doesn't fight a person who
// scrolled up to read earlier history.
// ---------------------------------------------------------------------
function updateChatPanel(state) {
  const moveLog = state.move_log || [];

  if (moveLog.length < lastChatMoveLogLength) {
    // A new game started with a shorter log than we last saw — start over.
    chatLogEl.innerHTML = "";
    lastChatMoveLogLength = 0;
  }

  const newEntries = moveLog.slice(lastChatMoveLogLength).filter(e => e.chat);
  lastChatMoveLogLength = moveLog.length;

  if (newEntries.length === 0) return;

  const stuckToBottom = chatLogEl.scrollTop + chatLogEl.clientHeight >= chatLogEl.scrollHeight - 20;

  for (const entry of newEntries) {
    const line = document.createElement("div");
    line.className = "chat-line";
    const nameSpan = document.createElement("span");
    nameSpan.className = "chat-name";
    nameSpan.textContent = (entry.name || entry.by || "anonymous") + ": ";
    line.appendChild(nameSpan);
    line.appendChild(document.createTextNode(entry.chat));
    // Every chat line here comes from move_log — show which move it was
    // attached to, the same way section 2 of SKILL.md describes it.
    const moveSpan = document.createElement("span");
    moveSpan.className = "chat-move";
    moveSpan.textContent = " (" + entry.san + ")";
    line.appendChild(moveSpan);
    chatLogEl.appendChild(line);
  }

  chatPanelEl.style.display = "flex";
  if (stuckToBottom) chatLogEl.scrollTop = chatLogEl.scrollHeight;
}

// ---------------------------------------------------------------------
// Chat input: lets a person at this page type a message that goes out
// attached to their next move (there's no standalone send — see
// submitMove()). Shown only while it's currently a 'web-user' side's
// own turn, since that's the only time a move (and so a chat line) can
// actually go out.
// ---------------------------------------------------------------------
function updateChatInput(state) {
  const show = state.started && !state.game_over && myTurnIsWebUser(state);
  chatInputRowEl.style.display = show ? "flex" : "none";
}

// ---------------------------------------------------------------------
// Resign / restart button: one button, whose label and effect depend on
// whether a person could be behind either side of the current game.
// ---------------------------------------------------------------------
function resignColorFor(state) {
  // Only meaningful when exactly one side is 'web-user': the page has no
  // notion of which color the person at the keyboard is playing, so this
  // only works if there's just one web-user side to assume it's them.
  // When both sides are 'web-user' (two people sharing this page, or one
  // person playing both), that assumption breaks down — see the two
  // explicit per-color buttons in updateGameControls() instead.
  if (!state || !state.started) return null;
  const whiteIsWeb = state.players.white === "web-user";
  const blackIsWeb = state.players.black === "web-user";
  if (whiteIsWeb && !blackIsWeb) return "white";
  if (blackIsWeb && !whiteIsWeb) return "black";
  return null;
}

function updateGameControls(state) {
  if (!state.started || state.game_over) {
    gameControlBtnEl.style.display = "none";
    resignBothRowEl.style.display = "none";
    return;
  }
  const whiteIsWeb = state.players.white === "web-user";
  const blackIsWeb = state.players.black === "web-user";
  if (whiteIsWeb && blackIsWeb) {
    // Neither "whoever's turn it is" nor any other guess can tell which
    // color the clicking person is playing, so offer both explicitly
    // instead of the single ambiguous button.
    gameControlBtnEl.style.display = "none";
    resignBothRowEl.style.display = "flex";
    resignWhiteBtnEl.onclick = () => doResign("white");
    resignBlackBtnEl.onclick = () => doResign("black");
    return;
  }
  resignBothRowEl.style.display = "none";
  const resignColor = resignColorFor(state);
  gameControlBtnEl.style.display = "";
  gameControlBtnEl.textContent = resignColor ? "Resign" : "Restart";
  gameControlBtnEl.onclick = () => (resignColor ? doResign(resignColor) : doRestart());
}

async function doResign(color) {
  if (!confirm("Resign as " + color + "? This ends the game for everyone watching.")) return;
  try {
    await fetch("/game/resign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ player: color }),
    });
    // The game-over state (and the start panel it brings back — see
    // render()) arrives through the SSE stream like any other change.
  } catch (e) {
    statusEl.textContent = "Could not reach the server to resign.";
  }
}

async function doRestart() {
  if (!confirm("Restart now? This ends the current game for everyone watching.")) return;
  try {
    await fetch("/game/abort", { method: "POST" });
    // The game-over state (and the start panel it brings back — see
    // render()) arrives through the SSE stream like any other change;
    // this call is what actually stops an engine-vs-engine match from
    // continuing to play while the new-game form is being filled out.
  } catch (e) {
    statusEl.textContent = "Could not reach the server to restart.";
    return;
  }
  startPanelEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
  evalBarEl.style.height = size + "px"; // eval bar always matches the board's own height
}
new ResizeObserver(fitBoard).observe(boardWrapEl);
window.addEventListener("resize", fitBoard);

// ---------------------------------------------------------------------
// Eval bar — a live Stockfish read on who is winning, from its own
// dedicated engine process (see game.py's ChessGame._ensure_eval_engine)
// entirely separate from any engine playing a side or answering a
// phone-a-friend query. state.eval is {"quality", "pov", "score_cp",
// "mate", "pending", "error"} — see GET /api/game in README.md.
// ---------------------------------------------------------------------

// Rough win-probability curve (the same shape chess sites use for their
// eval bars): centipawns -> White's share of the bar, 0..1. Not a
// calibrated probability, just a reasonable squashing function so a
// won position reads as "mostly full" rather than swinging wildly.
function whiteShareFromCp(scoreCp) {
  return 1 / (1 + Math.pow(10, -scoreCp / 400));
}

function updateEvalBar(state) {
  const ev = state.eval;
  if (!ev || ev.quality === "off") {
    evalBarWrapEl.style.display = "none";
    return;
  }
  evalBarWrapEl.style.display = "flex";

  const haveMate = ev.mate !== null && ev.mate !== undefined;
  const haveScore = ev.score_cp !== null && ev.score_cp !== undefined;
  let whiteShare = 0.5; // no data yet (right after a new game) — show a neutral bar
  if (haveMate) whiteShare = ev.mate > 0 ? 1 : 0;
  else if (haveScore) whiteShare = whiteShareFromCp(ev.score_cp);
  evalBarFillEl.style.height = (whiteShare * 100) + "%";

  let label = "\\u2026"; // ellipsis: still computing the first evaluation
  if (ev.error) label = "err";
  else if (haveMate) label = "M" + Math.abs(ev.mate);
  else if (haveScore) {
    const pawns = ev.score_cp / 100;
    label = (pawns >= 0 ? "+" : "") + pawns.toFixed(1);
  }
  evalBarLabelEl.textContent = label;
  evalBarWrapEl.title = ev.error ? ("Eval bar error: " + ev.error)
    : ev.pending ? "Evaluating\\u2026 (showing the last position's read)"
    : "Stockfish eval, White's perspective. Positive favors White.";

  // Reflect the current quality in the selector — it's a sticky,
  // server-side setting (see POST /game/eval-quality), so another tab
  // or an API caller changing it should show up here too.
  if (evalQualitySelEl.value !== ev.quality) {
    evalQualitySelEl.value = ev.quality;
    updateEvalQualityDesc(ev.quality);
  }
}

function updateEvalQualityDesc(qualityId) {
  const q = evalQualities.find(q => q.id === qualityId);
  evalQualityDescEl.textContent = q ? q.description : "";
}

async function initEvalControls() {
  try {
    const res = await fetch("/game/eval-qualities");
    const data = await res.json();
    evalQualities = data.qualities || [];
    fillSelect(evalQualitySelEl, evalQualities, data.default);
  } catch (e) {
    evalQualities = [];
  }
  updateEvalQualityDesc(evalQualitySelEl.value);
  evalQualitySelEl.addEventListener("change", async () => {
    const quality = evalQualitySelEl.value;
    updateEvalQualityDesc(quality);
    try {
      await fetch("/game/eval-quality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quality }),
      });
      // The new eval (or the "off" bar-hide) arrives through the SSE stream.
    } catch (e) {
      // Best-effort — the selector already reflects the intended choice;
      // the next state push will correct it if the request didn't land.
    }
  });
}

// ---------------------------------------------------------------------
// Start-a-new-game panel
// ---------------------------------------------------------------------
async function initStartPanel() {
  const whiteSel = document.getElementById("start-white");
  const blackSel = document.getElementById("start-black");
  const whiteEngineRow = document.getElementById("start-white-engine-row");
  const blackEngineRow = document.getElementById("start-black-engine-row");
  const whiteEngineSel = document.getElementById("start-white-engine");
  const blackEngineSel = document.getElementById("start-black-engine");
  const whiteLevelRow = document.getElementById("start-white-level-row");
  const blackLevelRow = document.getElementById("start-black-level-row");
  const whiteLevelSel = document.getElementById("start-white-level");
  const blackLevelSel = document.getElementById("start-black-level");
  const friendRow = document.getElementById("start-friend-row");
  const friendEnginesEl = document.getElementById("start-friend-engines");
  const errEl = document.getElementById("start-error");
  const startBtn = document.getElementById("start-btn");

  fillSelect(whiteSel, PLAYER_TYPES, "web-user");
  fillSelect(blackSel, PLAYER_TYPES, "engine");
  fillSelect(whiteEngineSel, ENGINE_TYPES, "gnuchess");
  fillSelect(blackEngineSel, ENGINE_TYPES, "gnuchess");

  // One independent L20/L10 budget pair per engine, so each engine's
  // phone-a-friend quota can be set separately (see game.py's
  // ENGINE_NAMES / DEFAULT_FRIEND_LIMITS — level 20: 1, level 10: 2 by
  // default, same starting point for every engine). Built dynamically
  // from ENGINE_TYPES so this scales to however many engines the server
  // supports, not just two.
  const friendInputs = {}; // engine id -> { l20, l10 }
  ENGINE_TYPES.forEach((eng) => {
    const l20 = document.createElement("input");
    l20.type = "number"; l20.min = "-1"; l20.max = "50"; l20.step = "1";
    l20.title = "Level-20 phone-a-friend queries allowed per api-user side, " + eng.label + " (-1 = unlimited)";
    l20.value = "1";
    const l20Tag = document.createElement("span");
    l20Tag.className = "friend-inputs-tag";
    l20Tag.textContent = "\\u00d7 L20";
    const l10 = document.createElement("input");
    l10.type = "number"; l10.min = "-1"; l10.max = "50"; l10.step = "1";
    l10.title = "Level-10 phone-a-friend queries allowed per api-user side, " + eng.label + " (-1 = unlimited)";
    l10.value = "2";
    const l10Tag = document.createElement("span");
    l10Tag.className = "friend-inputs-tag";
    l10Tag.textContent = "\\u00d7 L10";
    const engTag = document.createElement("span");
    engTag.className = "friend-inputs-tag friend-engine-name";
    engTag.textContent = eng.label;
    const l20Pair = document.createElement("span");
    l20Pair.className = "friend-inputs-pair";
    l20Pair.append(l20, l20Tag);
    const l10Pair = document.createElement("span");
    l10Pair.className = "friend-inputs-pair";
    l10Pair.append(l10, l10Tag);
    const pairsRow = document.createElement("div");
    pairsRow.className = "friend-engine-pairs";
    pairsRow.append(l20Pair, l10Pair);
    const engineRow = document.createElement("div");
    engineRow.className = "friend-engine-row";
    engineRow.append(engTag, pairsRow);
    friendEnginesEl.append(engineRow);
    friendInputs[eng.id] = { l20, l10 };
  });

  // The "eval" phone-a-friend kind (see FRIEND_KINDS in game.py) is not
  // one of the per-engine move-hint tiers above: it asks Stockfish who
  // is winning rather than what to play, and draws on its own budget, so
  // it gets its own row rather than a third box on the Stockfish one.
  const evalLimitInput = document.createElement("input");
  evalLimitInput.type = "number";
  evalLimitInput.min = "-1"; evalLimitInput.max = "50"; evalLimitInput.step = "1";
  evalLimitInput.title = "Full-strength Stockfish position evaluations allowed " +
    "per api-user side (-1 = unlimited)";
  evalLimitInput.value = "1";
  const evalTag = document.createElement("span");
  evalTag.className = "friend-inputs-tag";
  evalTag.textContent = "\\u00d7 eval";
  const evalPair = document.createElement("span");
  evalPair.className = "friend-inputs-pair";
  evalPair.append(evalLimitInput, evalTag);
  const evalPairsRow = document.createElement("div");
  evalPairsRow.className = "friend-engine-pairs";
  evalPairsRow.append(evalPair);
  const evalNameTag = document.createElement("span");
  evalNameTag.className = "friend-inputs-tag friend-engine-name";
  evalNameTag.textContent = "Stockfish eval";
  const evalRow = document.createElement("div");
  evalRow.className = "friend-engine-row";
  evalRow.append(evalNameTag, evalPairsRow);
  friendEnginesEl.append(evalRow);

  // Each side's engine and level controls are independent: an
  // engine-vs-engine game can (and often should, to be an interesting
  // game to watch) pit two different engines and/or difficulties
  // against each other. The "phone a friend" budget only matters if at
  // least one side will be 'api-user'/'api-trainee' — it's set once for
  // the whole game and tracked separately per side (see
  // POST /api/game/phone-a-friend).
  function isApiUserLike(value) {
    return value === "api-user" || value === "api-trainee";
  }
  function refreshLevelVisibility() {
    whiteEngineRow.style.display = whiteSel.value === "engine" ? "flex" : "none";
    blackEngineRow.style.display = blackSel.value === "engine" ? "flex" : "none";
    whiteLevelRow.style.display = whiteSel.value === "engine" ? "flex" : "none";
    blackLevelRow.style.display = blackSel.value === "engine" ? "flex" : "none";
    const anyApiUser = isApiUserLike(whiteSel.value) || isApiUserLike(blackSel.value);
    friendRow.style.display = anyApiUser ? "flex" : "none";
  }
  whiteSel.addEventListener("change", refreshLevelVisibility);
  blackSel.addEventListener("change", refreshLevelVisibility);
  refreshLevelVisibility();

  const fallbackLevels = Array.from({ length: 21 }, (_, i) => ({ id: String(i), label: "Level " + i }));
  try {
    const res = await fetch("/game/engine-levels");
    const data = await res.json();
    const min = data.min ?? 0, max = data.max ?? 20;
    const options = Array.from({ length: max - min + 1 }, (_, i) => ({ id: String(min + i), label: "Level " + (min + i) }));
    const defaultId = String(data.default ?? 10);
    fillSelect(whiteLevelSel, options.length ? options : fallbackLevels, defaultId);
    fillSelect(blackLevelSel, options.length ? options : fallbackLevels, defaultId);
  } catch (e) {
    fillSelect(whiteLevelSel, fallbackLevels, "10");
    fillSelect(blackLevelSel, fallbackLevels, "10");
  }

  startBtn.addEventListener("click", async () => {
    errEl.textContent = "";
    startBtn.textContent = "Starting\\u2026";
    const body = { white: whiteSel.value, black: blackSel.value };
    if (whiteSel.value === "engine") {
      body.white_level = parseInt(whiteLevelSel.value, 10);
      body.white_engine = whiteEngineSel.value;
    }
    if (blackSel.value === "engine") {
      body.black_level = parseInt(blackLevelSel.value, 10);
      body.black_engine = blackEngineSel.value;
    }
    if (isApiUserLike(whiteSel.value) || isApiUserLike(blackSel.value)) {
      const friendLimits = {};
      Object.entries(friendInputs).forEach(([engId, { l20, l10 }]) => {
        const tiers = {};
        if (l20.value !== "") tiers["20"] = parseInt(l20.value, 10);
        if (l10.value !== "") tiers["10"] = parseInt(l10.value, 10);
        if (Object.keys(tiers).length) friendLimits[engId] = tiers;
      });
      if (Object.keys(friendLimits).length) body.friend_limits = friendLimits;
      if (evalLimitInput.value !== "") {
        body.friend_eval_limit = parseInt(evalLimitInput.value, 10);
      }
    }
    try {
      const res = await fetch("/game/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        errEl.textContent = data.error || "Could not start the game.";
      }
      // On success, the new state arrives through the SSE stream, which
      // naturally hides the start panel again (state.started/game_over).
    } catch (e) {
      errEl.textContent = "Could not reach the server.";
    } finally {
      startBtn.textContent = "Start game";
    }
  });
}

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
  await initStartPanel();
  await initEvalControls();
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
        return jsonify(game.state(include_eval=True))

    @app.get("/events")
    def events():
        """Server-Sent Events stream: pushes the current state once on
        connect, then again every time the game actually changes (move,
        new game, resignation, ...) — no fixed-interval polling."""

        def generate():
            version = -1  # guarantees the first wait_for_change() returns immediately
            while True:
                payload, version = game.wait_for_change(version, timeout=20, include_eval=True)
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

    # ---- routes behind this page's own start-game / click-to-move UI ----
    # These are thin wrappers over the same shared `game` object the REST
    # API (port 5003, api.py) uses — same validation, same effect on the
    # game. They exist only so this page's JS can call them same-origin;
    # see the module docstring above for why. Kept out of api.py's
    # request/response shape so that reference stays exactly what an
    # agent following SKILL.md sees, with nothing viewer-specific in it.

    def _error(message, status=400):
        return jsonify(error=message), status

    @app.get("/game/engine-levels")
    def game_engine_levels():
        return jsonify(describe_levels())

    @app.get("/game/eval-qualities")
    def game_eval_qualities():
        return jsonify(describe_eval_qualities())

    @app.post("/game/eval-quality")
    def game_eval_quality():
        body = request.get_json(silent=True) or {}
        quality = body.get("quality")
        if not quality:
            return _error(f"'quality' is required (one of: {', '.join(EVAL_QUALITIES)})")
        try:
            result = game.set_eval_quality(quality)
        except GameError as e:
            return _error(str(e))
        return jsonify(result)

    @app.post("/game/start")
    def game_start():
        body = request.get_json(silent=True) or {}
        white = body.get("white", "web-user")
        black = body.get("black", "engine")
        level = body.get("level")
        white_level = body.get("white_level")
        black_level = body.get("black_level")
        engine = body.get("engine")
        white_engine = body.get("white_engine")
        black_engine = body.get("black_engine")
        friend_eval_limit = body.get("friend_eval_limit")
        friend_level10_limit = body.get("friend_level10_limit")
        friend_level20_limit = body.get("friend_level20_limit")
        friend_limits = body.get("friend_limits")
        try:
            engine_friend_limits = None
            if isinstance(friend_limits, dict):
                engine_friend_limits = {
                    name: {int(tier): limit for tier, limit in (tiers or {}).items()}
                    for name, tiers in friend_limits.items()
                }
        except (TypeError, ValueError, AttributeError):
            return _error("'friend_limits' must be an object of the form "
                           "{engine_name: {tier: limit}}")
        try:
            state, engine_move = game.new_game(
                white, black, level=level, white_level=white_level, black_level=black_level,
                engine=engine, white_engine=white_engine, black_engine=black_engine,
                friend_level10_limit=friend_level10_limit, friend_level20_limit=friend_level20_limit,
                friend_eval_limit=friend_eval_limit,
                engine_friend_limits=engine_friend_limits, include_eval=True,
            )
        except GameError as e:
            return _error(str(e))
        return jsonify(state=state, engine_move=engine_move), 201

    @app.post("/game/move")
    def game_move():
        body = request.get_json(silent=True) or {}
        move_str = body.get("move")
        chat = body.get("chat")
        if not move_str:
            return _error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        try:
            player_move, engine_move = game.make_move(move_str, chat=chat)
        except GameError as e:
            return _error(str(e))
        if player_move.get("forfeited"):
            return jsonify(forfeited=True, by=player_move["by"],
                            reasons=player_move["reasons"], state=game.state(include_eval=True))
        return jsonify(move=player_move, engine_move=engine_move, state=game.state(include_eval=True))

    @app.get("/game/legal-moves")
    def game_legal_moves():
        from_square = request.args.get("from")
        try:
            moves = game.legal_moves(from_square)
        except GameError as e:
            return _error(str(e), 404 if "no game" in str(e) else 400)
        return jsonify(moves=moves, count=len(moves))

    @app.post("/game/resign")
    def game_resign():
        """Backs the page's Resign button (shown when at least one side
        is 'web-user' — see the JS). A person acting through the browser
        has no color of their own to authenticate as, any more than an
        API user does (see the module docstring), so this accepts
        whichever color the button sent, same as the REST API's
        POST /api/game/resign."""
        body = request.get_json(silent=True) or {}
        player = body.get("player")
        try:
            state = game.resign(player, include_eval=True)
        except GameError as e:
            return _error(str(e))
        return jsonify(state=state)

    @app.post("/game/abort")
    def game_abort():
        """Backs the page's Restart button when no side is 'web-user'
        (an engine-vs-engine or api-user/engine game — see the JS'
        resignColorFor()): immediately ends the running game with no
        winner, same as the REST API's POST /api/game/abort, so an
        engine-vs-engine match actually stops instead of continuing to
        play while the new-game form is filled out."""
        try:
            state = game.abort(include_eval=True)
        except GameError as e:
            return _error(str(e))
        return jsonify(state=state)

    @app.get("/game/transcript")
    def game_transcript():
        """Backs the page's "Download transcript" button, shown once the
        game has ended (see transcriptBtnEl in the JS). Same underlying
        call as the REST API's GET /api/game/transcript — a downloadable
        PGN file, not JSON.

        This always serves the complete, fully annotated transcript, with
        every chat line, both reasoning fields, and the eval read on every
        move. Unlike the REST API's copy of this endpoint it takes no
        'include' parameter and offers no way to ask for less: the file is
        going to disk for a person to keep and review, so there is nothing
        to be gained by trimming it and a finished game's whole record to
        lose."""
        try:
            pgn = game.transcript(include_annotations=True)
        except GameError as e:
            return _error(str(e))
        return Response(
            pgn,
            mimetype="application/x-chess-pgn",
            headers={"Content-Disposition": 'attachment; filename="computer-chess.pgn"'},
        )

    return app
