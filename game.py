"""Core chess game state, plus GNU Chess engine integration.

This module has no Flask/HTTP dependency; it's the shared, thread-safe
model that both the REST API (port 5003) and the board viewer
(port 5004) sit on top of. Only one game exists at a time, per spec:
starting a new game replaces whatever game was previously in progress.

python-chess (the `chess` package) is used as the source of truth for
board state, legal-move generation, and SAN/UCI parsing, since it's far
more robust than screen-scraping GNU Chess's own text output. GNU Chess
itself (`gnuchess --uci`) is only used as the "engine" player, spoken to
over the UCI protocol via `chess.engine`.
"""

import os
import shutil
import threading
import time

import chess
import chess.engine

# GNU Chess's UCI mode doesn't expose a Stockfish-style "Skill Level"/Elo
# option (checked via `engine.options` — it only has search-tuning knobs
# like Hash, NullMove Pruning, etc.). The standard way to approximate a
# difficulty dial for a UCI engine like this is to cap how hard it's
# allowed to search: a shallow search-depth limit plays weak, obviously
# suboptimal moves, while a deep one plays strong. Each level pairs a
# depth cap with a generous time cap (a safety net in case some position
# is slow to search to the target depth, not the primary lever) — so
# actual strength/thinking time will vary a bit with position complexity,
# same as it would for any depth-limited engine.
LEVEL_MIN = 1
LEVEL_MAX = 10
DEFAULT_LEVEL = 5

LEVEL_TUNING = {
    1:  {"depth": 1,  "time": 0.2},
    2:  {"depth": 2,  "time": 0.3},
    3:  {"depth": 3,  "time": 0.4},
    4:  {"depth": 4,  "time": 0.6},
    5:  {"depth": 5,  "time": 0.8},
    6:  {"depth": 6,  "time": 1.2},
    7:  {"depth": 8,  "time": 1.8},
    8:  {"depth": 10, "time": 2.5},
    9:  {"depth": 12, "time": 3.5},
    10: {"depth": 15, "time": 5.0},
}

# A side is one of three types:
#   "api-user" — moves come from the REST API (port 5003), e.g. an agent or curl.
#   "web-user"  — moves come from a person clicking the board in the browser
#                 viewer (port 5004).
#   "engine"    — GNU Chess plays this side automatically.
# "api-user" and "web-user" behave identically to the game itself (both are
# just "a move shows up for this side eventually"); the distinction only
# matters for display (the "by" field on a move, and which side the viewer's
# click-to-move UI lets the current browser act on). Both sides can be
# "engine" — the two engines then play each other automatically, one side
# tuned to each side's own difficulty level (see ChessGame._start_autoplay).
PLAYER_TYPES = ("api-user", "web-user", "engine")

# Pause between moves when both sides are "engine" and the game is playing
# itself out in the background (see ChessGame._start_autoplay). Purely for
# spectator pacing — without it, a low-difficulty engine-vs-engine game
# would finish in well under a second and nobody watching the viewer would
# see it move.
AUTOPLAY_PAUSE_SECONDS = 1.0

# Display name and chat-message length caps. Both are trimmed rather than
# rejected outright — a display name or a short chat line is a cosmetic
# add-on to a move, not something worth failing the move over.
NAME_MAX_LEN = 40
MESSAGE_MAX_LEN = 240
# "reasoning" is a private note an API user can attach to their own move
# (see make_move()) — never returned by any endpoint, so it's allowed a
# little more room than a chat message.
REASONING_MAX_LEN = 1000

# Bounds for GET /api/game/wait's blocking wait — see ChessGame.wait_for_turn.
# The cap keeps a single request from tying up a server thread indefinitely
# (or running into a reverse proxy's own request timeout).
WAIT_DEFAULT_TIMEOUT_SECONDS = 25
WAIT_MAX_TIMEOUT_SECONDS = 55


def describe_levels():
    """List of {"level", "depth", "max_time_seconds"} for every valid
    level, plus the default — used by GET /api/engine-levels."""
    return {
        "levels": [
            {"level": lvl, "depth": t["depth"], "max_time_seconds": t["time"]}
            for lvl, t in sorted(LEVEL_TUNING.items())
        ],
        "default": DEFAULT_LEVEL,
    }


def _find_gnuchess():
    """Locate the gnuchess executable. On Debian/Ubuntu it installs to
    /usr/games, which is on PATH for interactive login shells but not
    always for processes spawned other ways — so PATH is checked first,
    then a couple of known fallback locations."""
    for candidate in (shutil.which("gnuchess"), "/usr/games/gnuchess", "/usr/bin/gnuchess"):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        "gnuchess executable not found (checked PATH, /usr/games, /usr/bin). "
        "Is the gnuchess apt package installed?"
    )


class GameError(Exception):
    """Raised for invalid game operations (bad move, wrong turn, no game, ...).

    The API layer catches this and turns it into a 4xx JSON error response.
    """


class ChessGame:
    def __init__(self):
        self._lock = threading.RLock()
        # Condition shares the same lock, so mutating state and notifying
        # waiters can happen atomically inside the `with self._lock:`
        # blocks below — no separate locking dance needed.
        self._change_cond = threading.Condition(self._lock)
        self._version = 0  # bumped on every state-changing operation
        self._generation = 0  # bumped on every new_game(); see _start_autoplay
        self._engine = None

        self.board = chess.Board()
        self.white_type = None       # "api-user" | "web-user" | "engine"
        self.black_type = None       # "api-user" | "web-user" | "engine"
        self.started = False
        self.result_reason = None    # set to "resigned" on resignation
        self.resigned_by = None      # "white" | "black"
        self.move_log = []           # [{"ply","color","uci","san","by","name","message","ts"}, ...]
        self.created_at = None
        # Standalone chat ("banter") — not attached to any particular
        # move, and visible to everyone, unlike self._reasoning_log below.
        # Reset every new_game() (see new_game()), since it's a
        # conversation about that specific game, not a sticky preference
        # like engine_levels or player_names.
        self.chat_log = []           # [{"seq","color","name","message","ts"}, ...]
        # Optional private note an API user can attach to their own move
        # via make_move()'s `reasoning` argument. Deliberately kept out of
        # move_log/state()/every other read endpoint — "not shared with
        # the other player" means not shared with anyone over the API,
        # since there is no authentication to tell players apart. Reset
        # every new_game(), same as chat_log.
        self._reasoning_log = []     # [{"ply","color","reasoning","ts"}, ...]
        # Difficulty is per side, not per game, so an engine-vs-engine game
        # can pit two different strengths against each other. For a game
        # with only one "engine" side, only that side's entry is ever read.
        self.engine_levels = {"white": DEFAULT_LEVEL, "black": DEFAULT_LEVEL}
        # Display name is also per side rather than per game — like
        # engine_levels, it's a sticky preference that carries over into
        # the next game unless overridden (see new_game()), since there's
        # no login for an API user to re-announce themselves with every
        # time. None means "no name set"; the UI then just shows the
        # side's type ("api-user", "engine", ...) instead.
        self.player_names = {"white": None, "black": None}

    # ---- engine lifecycle -------------------------------------------------

    def _ensure_engine(self):
        """Lazily start the gnuchess UCI process. Reused across the whole
        server lifetime (and across games) — only its move-time limit and
        the board position passed to `.play()` change per call."""
        if self._engine is None:
            self._engine = chess.engine.SimpleEngine.popen_uci([_find_gnuchess(), "--uci"])
        return self._engine

    def shutdown(self):
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None

    # ---- game lifecycle -----------------------------------------------------

    def new_game(self, white, black, level=None, white_level=None, black_level=None,
                 white_name=None, black_name=None):
        """Start a fresh game. `white`/`black` are each one of PLAYER_TYPES
        ('api-user', 'web-user', 'engine'); both can be 'engine'. `level`
        (optional, 1-10) sets the difficulty for both sides at once — a
        convenience for the common one-engine case. `white_level` and
        `black_level` (each optional, 1-10) set one side's difficulty
        specifically, and take priority over `level` for that side; use
        them to give the two engines in an engine-vs-engine game different
        strengths. Any level left unset keeps whatever was last set (or
        the default). `white_name`/`black_name` (each optional) likewise
        set that side's display name for this game, and otherwise keep
        whatever name was last set (see set_name()). Returns (state_dict,
        engine_move_or_None) — engine_move is set if white is 'engine',
        since it then moves immediately. If both sides are 'engine', the
        rest of the game plays out in the background — see
        _start_autoplay."""
        white = (white or "").strip().lower()
        black = (black or "").strip().lower()
        if white not in PLAYER_TYPES or black not in PLAYER_TYPES:
            raise GameError(f"'white' and 'black' must each be one of: {', '.join(PLAYER_TYPES)}")
        if level is not None:
            level = self._validate_level(level)
        if white_level is not None:
            white_level = self._validate_level(white_level)
        if black_level is not None:
            black_level = self._validate_level(black_level)
        if white_name is not None:
            white_name = self._clean_text(white_name, NAME_MAX_LEN)
        if black_name is not None:
            black_name = self._clean_text(black_name, NAME_MAX_LEN)

        with self._lock:
            self.board = chess.Board()
            self.white_type = white
            self.black_type = black
            self.started = True
            self.result_reason = None
            self.resigned_by = None
            self.move_log = []
            self.chat_log = []
            self._reasoning_log = []
            self.created_at = time.time()
            self._generation += 1
            generation = self._generation
            if level is not None:
                self.engine_levels["white"] = level
                self.engine_levels["black"] = level
            if white_level is not None:
                self.engine_levels["white"] = white_level
            if black_level is not None:
                self.engine_levels["black"] = black_level
            if white_name is not None:
                self.player_names["white"] = white_name
            if black_name is not None:
                self.player_names["black"] = black_name

            both_engines = white == "engine" and black == "engine"
            engine_move = None
            if not both_engines and self._current_player_type() == "engine":
                # Exactly one side is 'engine': play its move synchronously,
                # same as before — the response's 'engine_move' reflects it.
                engine_move = self._play_engine_move_locked()

            self._bump_version_locked()
            state = self.state()

        if both_engines:
            # Neither side will ever call POST /api/game/move, so nothing
            # else would ever advance this game. Play it out in the
            # background instead, one paced move at a time (see
            # AUTOPLAY_PAUSE_SECONDS), so it streams to the viewer like any
            # other game rather than being fully decided before this
            # request even returns.
            self._start_autoplay(generation)

        return state, engine_move

    def _start_autoplay(self, generation):
        """Spawn a background thread that keeps playing engine moves for
        the game started at `generation` (see self._generation) until it
        ends or is replaced by a newer game. Only used for engine-vs-engine
        games — see new_game()."""

        def run():
            while True:
                time.sleep(AUTOPLAY_PAUSE_SECONDS)
                with self._lock:
                    if self._generation != generation:
                        return  # this game was replaced by a newer one
                    if self._status() != "in_progress":
                        return  # checkmate, draw, or a resignation
                    entry = self._play_engine_move_locked()
                    self._bump_version_locked()
                if entry is None:
                    return

        threading.Thread(target=run, daemon=True, name="engine-autoplay").start()

    # ---- internal helpers -------------------------------------------------

    def _bump_version_locked(self):
        """Caller must hold self._lock. Marks the state as changed and
        wakes anyone blocked in wait_for_change() (used by the SSE stream
        the viewer listens on)."""
        self._version += 1
        self._change_cond.notify_all()

    def _validate_level(self, level):
        try:
            level = int(level)
        except (TypeError, ValueError):
            raise GameError(f"'level' must be an integer between {LEVEL_MIN} and {LEVEL_MAX}")
        if not (LEVEL_MIN <= level <= LEVEL_MAX):
            raise GameError(f"'level' must be between {LEVEL_MIN} and {LEVEL_MAX}")
        return level

    def _clean_text(self, text, max_len):
        """Strip and cap free-form text (a display name or a chat message)
        to `max_len` characters. Returns None for empty/whitespace-only
        input, so callers can use that to mean 'no name' or 'no message'
        rather than raising an error over what is, at worst, a cosmetic
        problem — a move should never fail just because its chat text was
        too long or blank."""
        if text is None:
            return None
        text = " ".join(str(text).split())  # collapse all whitespace, incl. newlines
        text = text[:max_len].strip()
        return text or None

    def _current_player_type(self):
        return self.white_type if self.board.turn == chess.WHITE else self.black_type

    def _game_over_reason(self):
        if self.result_reason == "resigned":
            return "resigned"
        board = self.board
        if board.is_checkmate():
            return "checkmate"
        if board.is_stalemate():
            return "stalemate"
        if board.is_insufficient_material():
            return "draw_insufficient_material"
        if board.is_seventyfive_moves():
            return "draw_75_moves"
        if board.is_fivefold_repetition():
            return "draw_5fold_repetition"
        if board.can_claim_fifty_moves():
            return "draw_claimable_50_moves"
        if board.can_claim_threefold_repetition():
            return "draw_claimable_threefold_repetition"
        return None

    def _status(self):
        if not self.started:
            return "not_started"
        return self._game_over_reason() or "in_progress"

    def _winner(self):
        if self.result_reason == "resigned":
            return "black" if self.resigned_by == "white" else "white"
        if self.board.is_checkmate():
            # side to move is the side that got mated
            return "black" if self.board.turn == chess.WHITE else "white"
        return None

    def _board_grid(self):
        """8x8 grid, row 0 = rank 8 (black's back rank) down to row 7 = rank 1,
        each column 0..7 = file a..h. Cell is None (empty) or
        {"color": "white"|"black", "type": "P"|"N"|"B"|"R"|"Q"|"K", "code": "wP"}.
        `code` is meant to line up with future piece image filenames, e.g.
        static/pieces/wN.png for the white knight."""
        board = self.board
        grid = []
        for rank in range(7, -1, -1):
            row = []
            for file in range(8):
                piece = board.piece_at(chess.square(file, rank))
                if piece is None:
                    row.append(None)
                else:
                    color = "white" if piece.color == chess.WHITE else "black"
                    ptype = piece.symbol().upper()
                    row.append({
                        "color": color,
                        "type": ptype,
                        "code": ("w" if color == "white" else "b") + ptype,
                    })
            grid.append(row)
        return grid

    def _parse_move(self, move_str):
        move_str = (move_str or "").strip()
        if not move_str:
            raise GameError("'move' must not be empty")
        try:
            return self.board.parse_uci(move_str)
        except ValueError:
            pass
        try:
            return self.board.parse_san(move_str)
        except ValueError:
            raise GameError(
                f"'{move_str}' is not a legal move in the current position "
                "(expected UCI like 'e2e4'/'e7e8q', or SAN like 'e4'/'Nf3')"
            )

    def _play_engine_move_locked(self):
        """Caller must hold self._lock. Asks gnuchess for its move in the
        current position and applies it, at the difficulty set for
        whichever color is on move (self.engine_levels). Returns the move
        log entry, or None if gnuchess had no move to offer (shouldn't
        happen while the game is in progress, but handled defensively)."""
        engine = self._ensure_engine()
        color = "white" if self.board.turn == chess.WHITE else "black"
        level = self.engine_levels.get(color, DEFAULT_LEVEL)
        tuning = LEVEL_TUNING.get(level, LEVEL_TUNING[DEFAULT_LEVEL])
        limit = chess.engine.Limit(depth=tuning["depth"], time=tuning["time"])
        result = engine.play(self.board, limit)
        move = result.move
        if move is None:
            return None
        san = self.board.san(move)
        uci = move.uci()
        self.board.push(move)
        # A custom name (set via set_name()) wins even for an 'engine' side;
        # otherwise fall back to a plain "GNU Chess" label so the viewer and
        # any chat log always have something readable to show.
        name = self.player_names.get(color) or "GNU Chess"
        entry = {"ply": len(self.move_log) + 1, "color": color, "uci": uci, "san": san,
                  "by": "engine", "name": name, "ts": time.time()}
        self.move_log.append(entry)
        return entry

    # ---- public, lock-guarded API ------------------------------------------

    def is_started(self):
        with self._lock:
            return self.started

    def set_level(self, level, color=None):
        """Change the engine's difficulty (1-10). `color` ('white' or
        'black', optional) targets one side only — use this for an
        engine-vs-engine game, where the two sides can differ. Omit
        `color` to set both sides at once, which is all that matters for
        a game with only one 'engine' side. Takes effect starting with
        that side's next move. Can be called whether or not a game is
        currently running. Returns the updated {"white": N, "black": N}."""
        level = self._validate_level(level)
        if color is not None and color not in ("white", "black"):
            raise GameError("'color' must be 'white' or 'black'")
        with self._lock:
            if color is None:
                self.engine_levels["white"] = level
                self.engine_levels["black"] = level
            else:
                self.engine_levels[color] = level
            self._bump_version_locked()
            return dict(self.engine_levels)

    def set_name(self, color, name):
        """Set (or clear) a side's display name. `color` is 'white' or
        'black'. `name` is shown in the board viewer and stamped onto
        that side's move-log entries from then on; pass None (or an
        empty/whitespace-only string) to clear it back to showing just
        the side's type. Like set_level(), this is a sticky per-side
        setting: it carries over into the next game unless overridden
        there (see new_game()'s white_name/black_name), and can be
        called whether or not a game is currently running — useful for
        an API user who is joining a game they did not start. Returns
        the updated {"white": name_or_None, "black": name_or_None}."""
        if color not in ("white", "black"):
            raise GameError("'color' must be 'white' or 'black'")
        name = self._clean_text(name, NAME_MAX_LEN)
        with self._lock:
            self.player_names[color] = name
            self._bump_version_locked()
            return dict(self.player_names)

    def wait_for_change(self, since_version, timeout=25):
        """Block until the game state has changed since `since_version`
        (or `timeout` seconds elapse), then return (state_dict, version).

        Pass `since_version=-1` (or any value that can't match a real
        version) to get the current state back immediately on first call.
        Used by the SSE stream in viewer.py so it can push updates the
        instant a move happens, rather than polling on a fixed interval.
        """
        with self._lock:
            if since_version != self._version:
                return self.state(), self._version
            self._change_cond.wait(timeout)
            return self.state(), self._version

    def wait_for_turn(self, color, timeout=None):
        """Block until it is `color`'s turn to move, the game ends, or
        `timeout` seconds elapse (default WAIT_DEFAULT_TIMEOUT_SECONDS,
        capped at WAIT_MAX_TIMEOUT_SECONDS) — whichever comes first.
        Returns the state at that point; the caller should check
        `state["turn"]` themselves, since a timeout looks the same as any
        other return here. Used by GET /api/game/wait so an API user can
        wait for their opponent's move with a single blocking call
        instead of a poll loop.

        Returns immediately, without blocking at all, if it is already
        `color`'s turn, the game has already ended, or no game has
        started — there is nothing to wait for in any of those cases.
        """
        if color not in ("white", "black"):
            raise GameError("'color' must be 'white' or 'black'")
        timeout = WAIT_DEFAULT_TIMEOUT_SECONDS if timeout is None else float(timeout)
        timeout = max(0.0, min(timeout, WAIT_MAX_TIMEOUT_SECONDS))
        deadline = time.time() + timeout

        with self._lock:
            state = self.state()
            version = self._version

        while True:
            if not state["started"] or state["game_over"] or state["turn"] == color:
                return state
            remaining = deadline - time.time()
            if remaining <= 0:
                return state
            state, version = self.wait_for_change(version, timeout=remaining)

    def state(self):
        with self._lock:
            board = self.board
            status = self._status()
            game_over = status not in ("not_started", "in_progress")
            return {
                "started": self.started,
                "status": status,
                "game_over": game_over,
                "winner": self._winner() if game_over else None,
                "turn": "white" if board.turn == chess.WHITE else "black",
                "in_check": board.is_check(),
                "fen": board.fen(),
                "board_ascii": str(board),
                "board": self._board_grid(),
                "players": {"white": self.white_type, "black": self.black_type},
                "player_names": dict(self.player_names),
                "engine_levels": dict(self.engine_levels),
                "fullmove_number": board.fullmove_number,
                "halfmove_clock": board.halfmove_clock,
                "move_log": list(self.move_log),
                "chat_log": list(self.chat_log),
            }

    def legal_moves(self, from_square=None):
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if from_square:
                from_square = from_square.strip().lower()
                if from_square not in chess.SQUARE_NAMES:
                    raise GameError(f"'{from_square}' is not a valid square (expected e.g. 'e2')")
            moves = []
            for m in self.board.legal_moves:
                if from_square and chess.square_name(m.from_square) != from_square:
                    continue
                moves.append({
                    "uci": m.uci(),
                    "san": self.board.san(m),
                    "from": chess.square_name(m.from_square),
                    "to": chess.square_name(m.to_square),
                    "promotion": chess.piece_symbol(m.promotion).upper() if m.promotion else None,
                })
            return moves

    def make_move(self, move_str, message=None, reasoning=None):
        """Submit a move for whichever side is currently to move — works
        the same whether that side is 'api-user' or 'web-user'; only
        'engine' turns are rejected here (gnuchess moves itself).

        `message` (optional) is a short chat line attached to this move —
        it and this side's current display name (see set_name()) are
        stamped onto the move-log entry, so anyone who reads the game
        state after this point (in particular, the opponent's own next
        call) sees them; there is no separate delivery step.

        `reasoning` (optional) is a private note about why this move was
        chosen. Unlike `message`, it is never returned by this or any
        other method — it is only kept in self._reasoning_log, which no
        endpoint reads from. See that attribute's comment in __init__.

        Returns (player_move_entry, engine_entry_or_None) — the engine
        entry is set if, after this move, it becomes an 'engine' side's
        turn and gnuchess replies immediately."""
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            mover_type = self._current_player_type()
            if mover_type == "engine":
                raise GameError("it is the engine's turn; wait for its move")

            move = self._parse_move(move_str)
            color = "white" if self.board.turn == chess.WHITE else "black"
            san = self.board.san(move)
            uci = move.uci()
            self.board.push(move)
            ply = len(self.move_log) + 1
            player_entry = {"ply": ply, "color": color, "uci": uci, "san": san,
                              "by": mover_type, "name": self.player_names.get(color),
                              "ts": time.time()}
            clean_message = self._clean_text(message, MESSAGE_MAX_LEN)
            if clean_message is not None:
                player_entry["message"] = clean_message
            self.move_log.append(player_entry)

            clean_reasoning = self._clean_text(reasoning, REASONING_MAX_LEN)
            if clean_reasoning is not None:
                self._reasoning_log.append(
                    {"ply": ply, "color": color, "reasoning": clean_reasoning, "ts": time.time()}
                )

            engine_entry = None
            if self._status() == "in_progress" and self._current_player_type() == "engine":
                engine_entry = self._play_engine_move_locked()

            self._bump_version_locked()
            return player_entry, engine_entry

    def send_chat(self, color, message):
        """Add a standalone chat line ("banter") — not attached to any
        particular move, unlike `message` on make_move(). `color` is
        'white' or 'black'; the message is stamped with that side's
        current display name (see set_name()). Visible to everyone who
        reads the game state (state()["chat_log"]), including a person
        watching the board viewer. Requires a game to exist (so there is
        something for the chat to be about), but not that it still be in
        progress — a "gg" after the game ends is fine. Returns the
        created entry."""
        if color not in ("white", "black"):
            raise GameError("'color' must be 'white' or 'black'")
        clean_message = self._clean_text(message, MESSAGE_MAX_LEN)
        if clean_message is None:
            raise GameError("'message' is required")
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            entry = {
                "seq": len(self.chat_log) + 1,
                "color": color,
                "name": self.player_names.get(color),
                "message": clean_message,
                "ts": time.time(),
            }
            self.chat_log.append(entry)
            self._bump_version_locked()
            return entry

    def resign(self, player):
        player = (player or "").strip().lower()
        if player not in ("white", "black"):
            raise GameError("'player' must be 'white' or 'black'")
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            self.result_reason = "resigned"
            self.resigned_by = player
            self._bump_version_locked()
            return self.state()
