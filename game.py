"""Core chess game state, plus chess-engine integration.

This module has no Flask/HTTP dependency; it's the shared, thread-safe
model that both the REST API (port 5003) and the board viewer
(port 5004) sit on top of. Only one game exists at a time, per spec:
starting a new game replaces whatever game was previously in progress.

python-chess (the `chess` package) is used as the source of truth for
board state, legal-move generation, and SAN/UCI parsing, since it's far
more robust than screen-scraping an engine's own text output. Two
engines are available to play the "engine" side of a game, chosen per
side: GNU Chess and Stockfish, both open source and both spoken to
over the UCI protocol via `chess.engine`. They are equally supported —
any side that can be `"engine"` can be either one.
"""

import os
import shutil
import threading
import time

import chess
import chess.engine

# Both engines share one difficulty scale: Stockfish's own native
# "Skill Level" UCI option, 0 (weakest) to 20 (strongest). Stockfish is
# set to it directly. GNU Chess has no such option (checked via
# `engine.options` — it only has search-tuning knobs like Hash, NullMove
# Pruning, etc.), so its difficulty is approximated the standard way for
# a UCI engine without one: a search-depth cap derived from the same
# 0-20 level, with a generous time cap as a safety net rather than the
# primary lever. See _search_limit_for().
LEVEL_MIN = 0
LEVEL_MAX = 20
DEFAULT_LEVEL = 10

# The two engines a side can be, when that side's type is "engine" (see
# PLAYER_TYPES below). Both are open source and installed in the image
# (see Dockerfile); neither is treated as the "real" one — either can
# play either color, alone or against each other, with independent
# levels. DEFAULT_ENGINE is used wherever an engine choice is left
# unspecified, purely so existing callers that never mention an engine
# name keep getting the same engine they always did.
ENGINE_NAMES = ("gnuchess", "stockfish")
DEFAULT_ENGINE = "gnuchess"
ENGINE_DISPLAY_NAMES = {"gnuchess": "GNU Chess", "stockfish": "Stockfish"}

# "Phone a friend": an "api-user" side to move can ask the server for
# an engine's own recommended move in the current position, without
# submitting it — a hint, not a move. See ChessGame.phone_a_friend().
# Only these two tiers on the shared 0-20 scale are offered ("call a
# weaker friend" vs. "call a strong friend"), each budgeted separately
# per side, per game, so an API user can't just re-ask a level-20
# friend a hundred times.
FRIEND_LEVELS = (10, 20)
DEFAULT_FRIEND_LIMITS = {10: 2, 20: 1}
FRIEND_LIMIT_MIN = 0  # 0 disables that tier entirely for the game
FRIEND_LIMIT_MAX = 50  # sane ceiling; this is a hint budget, not a real resource

# A side is one of three types:
#   "api-user" — moves come from the REST API (port 5003), e.g. an agent or curl.
#   "web-user"  — moves come from a person clicking the board in the browser
#                 viewer (port 5004).
#   "engine"    — one of ENGINE_NAMES plays this side automatically; which
#                 one is a separate, per-side choice (see engine_names).
# "api-user" and "web-user" behave identically to the game itself (both are
# just "a move shows up for this side eventually"); the distinction only
# matters for display (the "by" field on a move, and which side the viewer's
# click-to-move UI lets the current browser act on). Both sides can be
# "engine" — the two engines then play each other automatically, one side
# tuned to each side's own difficulty level and engine choice (see
# ChessGame._start_autoplay).
PLAYER_TYPES = ("api-user", "web-user", "engine")

# Pause between moves when both sides are "engine" and the game is playing
# itself out in the background (see ChessGame._start_autoplay). Purely for
# spectator pacing — without it, a low-difficulty engine-vs-engine game
# would finish in well under a second and nobody watching the viewer would
# see it move.
AUTOPLAY_PAUSE_SECONDS = 1.0

# Display name and chat-message length caps. Both are trimmed rather than
# rejected outright — a display name or a short chat line is a cosmetic
# add-on to a move, not something worth failing the move over. Chat is
# always attached to a move (the `chat` argument to make_move()) — there
# is no standalone/banter channel; see the removed send_chat()/
# POST /api/game/chat in the module history if you're looking for one.
NAME_MAX_LEN = 40
CHAT_MAX_LEN = 240
# "reasoning" is a private note an API user can attach to their own move
# (see make_move()) — never returned by any endpoint *while the game is
# in progress*, so it's allowed a little more room than a chat message.
# The one exception is transcript() (see below): once a game has ended,
# there is no ongoing competitive advantage left to protect, so a
# finished game's transcript folds reasoning in as PGN comments.
REASONING_MAX_LEN = 1000

# Statuses state_status()/transcript() treat as "the game has ended" —
# anything _game_over_reason() can return. Kept as one tuple so
# transcript()'s "has this game actually finished?" check and any future
# caller can share the same list instead of re-deriving it.
FINISHED_STATUSES = (
    "checkmate",
    "stalemate",
    "draw_insufficient_material",
    "draw_75_moves",
    "draw_5fold_repetition",
    "draw_claimable_50_moves",
    "draw_claimable_threefold_repetition",
    "resigned",
)

# transcript()'s PGN "Termination" tag — a standard supplementary PGN tag
# (used by lichess.org, chess.com, and most PGN-writing tools) that says
# in plain words why the game ended, distinct from the bare Result tag
# ("1-0"/"0-1"/"1/2-1/2").
TERMINATION_LABELS = {
    "checkmate": "checkmate",
    "stalemate": "stalemate",
    "draw_insufficient_material": "draw by insufficient material",
    "draw_75_moves": "draw by 75-move rule",
    "draw_5fold_repetition": "draw by fivefold repetition",
    "draw_claimable_50_moves": "draw (50-move rule claimable)",
    "draw_claimable_threefold_repetition": "draw (threefold repetition claimable)",
    "resigned": "resignation",
}

# transcript()'s PGN White/Black tags fall back to this when a side has
# no display name set (see set_name()) — a human-readable stand-in for
# the raw PLAYER_TYPES string. An "engine" side's fallback is looked up
# from ENGINE_DISPLAY_NAMES instead (see transcript()), since which
# engine it is matters more than the generic "engine" type string.
TYPE_LABELS = {"api-user": "API user", "web-user": "Web user"}

# Bounds for GET /api/game/wait's blocking wait — see ChessGame.wait_for_turn.
# The cap keeps a single request from tying up a server thread indefinitely
# (or running into a reverse proxy's own request timeout).
WAIT_DEFAULT_TIMEOUT_SECONDS = 25
WAIT_MAX_TIMEOUT_SECONDS = 55


def describe_levels():
    """{"min", "max", "default", "engines"} describing the shared 0-20
    difficulty scale (Stockfish's own native "Skill Level") that both
    engines use — see GET /api/engine-levels and LEVEL_MIN/MAX above."""
    return {
        "min": LEVEL_MIN,
        "max": LEVEL_MAX,
        "default": DEFAULT_LEVEL,
        "engines": list(ENGINE_NAMES),
    }


def _find_executable(names, candidates):
    """Locate the first existing, executable path among `candidates`
    (each optional). `names` is only used for the error message."""
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(
        f"none of {names} found (checked PATH and common install locations). "
        "Is the corresponding apt package installed?"
    )


def _find_gnuchess():
    """Locate the gnuchess executable. On Debian/Ubuntu it installs to
    /usr/games, which is on PATH for interactive login shells but not
    always for processes spawned other ways — so PATH is checked first,
    then a couple of known fallback locations."""
    return _find_executable(
        "gnuchess",
        (shutil.which("gnuchess"), "/usr/games/gnuchess", "/usr/bin/gnuchess"),
    )


def _find_stockfish():
    """Locate the stockfish executable — same PATH-then-fallback search
    as _find_gnuchess(), since the Debian/Ubuntu stockfish package
    installs to the same /usr/games location."""
    return _find_executable(
        "stockfish",
        (shutil.which("stockfish"), "/usr/games/stockfish", "/usr/bin/stockfish"),
    )


_ENGINE_FINDERS = {"gnuchess": _find_gnuchess, "stockfish": _find_stockfish}


def _search_limit_for(engine_name, level):
    """chess.engine.Limit for one engine's move/hint search at `level`
    (0-20, see LEVEL_MIN/MAX). Both engines share the same time budget,
    scaled linearly across the level range as a spectator-pacing and
    safety-net cap. GNU Chess additionally gets a depth cap, since a
    search-depth limit is its *only* real difficulty lever (see the
    module comment above LEVEL_MIN); Stockfish's difficulty comes from
    its own "Skill Level" option (set separately — see
    ChessGame._configure_engine_locked), so it just gets the time cap.
    """
    time_limit = 0.2 + (level - LEVEL_MIN) * 4.8 / (LEVEL_MAX - LEVEL_MIN)
    if engine_name == "gnuchess":
        depth = 1 + round((level - LEVEL_MIN) * 14 / (LEVEL_MAX - LEVEL_MIN))
        return chess.engine.Limit(depth=depth, time=time_limit)
    return chess.engine.Limit(time=time_limit)


class GameError(Exception):
    """Raised for invalid game operations (bad move, wrong turn, no game, ...).

    The API layer catches this and turns it into a 4xx JSON error response.
    """


# ---- PGN (Portable Game Notation) helpers, for ChessGame.transcript() ----
# PGN is the standard plain-text chess game format — the same one
# lichess.org, chess.com, and every chess GUI import/export. It has two
# parts: a block of `[Tag "value"]` metadata lines, then movetext (move
# numbers and SAN moves) with the result at the end. A move can carry a
# free-text `{comment}` right after it, which is where this module tucks
# in chat and reasoning. See https://en.wikipedia.org/wiki/Portable_Game_Notation.

def _pgn_escape_tag(value):
    """Escape a value for a `[Tag "value"]` line: PGN only requires
    backslash and double-quote to be escaped inside a tag string."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _pgn_escape_comment(text):
    """Collapse whitespace and strip the one character PGN comments
    can't contain unescaped: an unmatched `{`/`}` would end the comment
    early or break the parser, so both are swapped for parens instead of
    trying to escape them (PGN has no escape sequence for braces)."""
    text = " ".join(str(text).split())
    return text.replace("{", "(").replace("}", ")")


def _wrap_pgn_movetext(text, width=80):
    """Soft-wrap movetext at `width` columns, breaking only on spaces.
    Not required by the PGN spec (files up to 255 columns are legal),
    but it's the convention most PGN-writing tools follow, and it keeps
    a downloaded transcript readable in a plain text editor."""
    words = text.split(" ")
    lines, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return "\n".join(lines)


class ChessGame:
    def __init__(self):
        self._lock = threading.RLock()
        # Condition shares the same lock, so mutating state and notifying
        # waiters can happen atomically inside the `with self._lock:`
        # blocks below — no separate locking dance needed.
        self._change_cond = threading.Condition(self._lock)
        self._version = 0  # bumped on every state-changing operation
        self._generation = 0  # bumped on every new_game(); see _start_autoplay
        self._engines = {}  # engine name -> chess.engine.SimpleEngine, lazily started

        self.board = chess.Board()
        self.white_type = None       # "api-user" | "web-user" | "engine"
        self.black_type = None       # "api-user" | "web-user" | "engine"
        self.started = False
        self.result_reason = None    # set to "resigned" on resignation
        self.resigned_by = None      # "white" | "black"
        self.move_log = []           # [{"ply","color","uci","san","by","name","chat","ts"}, ...]
        self.created_at = None
        # Optional private note an API user can attach to their own move
        # via make_move()'s `reasoning` argument. Kept out of move_log and
        # every other read endpoint *while the game is in progress* —
        # "not shared with the other player" means not shared with anyone
        # over the API, since there is no authentication to tell players
        # apart. The one exception is transcript() (see below): once a
        # game has ended, reasoning is folded into that game's PGN
        # transcript as move comments, since there's no ongoing
        # competitive edge left to protect at that point. Reset every
        # new_game().
        self._reasoning_log = []     # [{"ply","color","reasoning","ts"}, ...]
        # Difficulty is per side, not per game, so an engine-vs-engine game
        # can pit two different strengths against each other. For a game
        # with only one "engine" side, only that side's entry is ever read.
        self.engine_levels = {"white": DEFAULT_LEVEL, "black": DEFAULT_LEVEL}
        # Which engine (see ENGINE_NAMES) plays each "engine" side — also
        # per side, so an engine-vs-engine game can pit GNU Chess against
        # Stockfish, not just two strengths of the same engine. For a game
        # with only one "engine" side, only that side's entry is ever read.
        self.engine_names = {"white": DEFAULT_ENGINE, "black": DEFAULT_ENGINE}
        # Display name is per side, but unlike engine_levels/engine_names
        # it is *not* sticky across games: it's reset to "no name" at the
        # start of every new_game() (see there), and only set for that
        # game if white_name/black_name is passed, or set_name() is
        # called afterward. A fresh game always starts with neither side
        # named. None means "no name set"; the UI then just shows the
        # side's type ("api-user", "engine", ...) instead.
        self.player_names = {"white": None, "black": None}
        # "Phone a friend" budget — see FRIEND_LEVELS/phone_a_friend()
        # below. Quotas are tracked separately per engine (see
        # ENGINE_NAMES), not pooled, so an 'api-user' side can draw on
        # GNU Chess hints and Stockfish hints independently rather than
        # the two competing for one shared budget. Like player_names
        # (and unlike engine_levels/engine_names), this is *not* sticky
        # across games: it's a per-game resource budget, set fresh at
        # each new_game() (defaulting to DEFAULT_FRIEND_LIMITS for each
        # engine), and usage always resets to zero.
        self.friend_limits = {
            name: dict(DEFAULT_FRIEND_LIMITS) for name in ENGINE_NAMES
        }  # {"gnuchess": {10: N, 20: N}, "stockfish": {10: N, 20: N}}
        self.friend_used = {
            "white": {name: {10: 0, 20: 0} for name in ENGINE_NAMES},
            "black": {name: {10: 0, 20: 0} for name in ENGINE_NAMES},
        }

    # ---- engine lifecycle -------------------------------------------------

    def _ensure_engine(self, engine_name):
        """Lazily start a UCI process for `engine_name` (see
        ENGINE_NAMES). Reused across the whole server lifetime (and
        across games) — one persistent process per engine, shared by
        both colors and by phone_a_friend(); only the search limit and
        (for Stockfish) the configured Skill Level change per call."""
        if engine_name not in ENGINE_NAMES:
            raise GameError(f"'engine' must be one of: {', '.join(ENGINE_NAMES)}")
        engine = self._engines.get(engine_name)
        if engine is None:
            path = _ENGINE_FINDERS[engine_name]()
            engine = chess.engine.SimpleEngine.popen_uci([path, "--uci"] if engine_name == "gnuchess" else path)
            self._engines[engine_name] = engine
        return engine

    def _configure_engine_locked(self, engine, engine_name, level):
        """Caller must hold self._lock. Applies `level` (0-20) to
        `engine` for its next move, for the engines that need per-call
        configuration rather than a plain search limit. Stockfish's
        native "Skill Level" option is the whole point of the shared
        0-20 scale (see LEVEL_MIN/MAX) — it's re-set here before every
        move/hint, since the same persistent Stockfish process is
        shared by both colors, which can hold different levels."""
        if engine_name == "stockfish":
            engine.configure({"Skill Level": level})

    def shutdown(self):
        with self._lock:
            for engine in self._engines.values():
                try:
                    engine.quit()
                except Exception:
                    pass
            self._engines = {}

    # ---- game lifecycle -----------------------------------------------------

    def new_game(self, white, black, level=None, white_level=None, black_level=None,
                 engine=None, white_engine=None, black_engine=None,
                 white_name=None, black_name=None,
                 friend_level10_limit=None, friend_level20_limit=None,
                 gnuchess_friend_level10_limit=None, gnuchess_friend_level20_limit=None,
                 stockfish_friend_level10_limit=None, stockfish_friend_level20_limit=None):
        """Start a fresh game. `white`/`black` are each one of PLAYER_TYPES
        ('api-user', 'web-user', 'engine'); both can be 'engine'. `level`
        (optional, 0-20, Stockfish's native "Skill Level" scale — see
        LEVEL_MIN/MAX) sets the difficulty for both sides at once — a
        convenience for the common one-engine case. `white_level` and
        `black_level` (each optional, 0-20) set one side's difficulty
        specifically, and take priority over `level` for that side; use
        them to give the two engines in an engine-vs-engine game different
        strengths. `engine` (optional, one of ENGINE_NAMES: 'gnuchess' or
        'stockfish') picks which engine plays both 'engine' sides at
        once; `white_engine`/`black_engine` (each optional) pick one
        side's engine specifically and win over `engine` for that side —
        use them to pit GNU Chess against Stockfish. Any level or engine
        left unset keeps whatever was last set (or the default — see
        DEFAULT_LEVEL/DEFAULT_ENGINE). `white_name`/`black_name` (each
        optional) set that side's display name for this game; every new
        game starts with neither side named, regardless of what was set
        for the previous game, so leaving one unset means that side has
        no name (see set_name() to name it after the fact).
        `friend_level10_limit` and `friend_level20_limit` (each optional,
        integers, default DEFAULT_FRIEND_LIMITS) set this game's "phone a
        friend" budget — see phone_a_friend() — for the two FRIEND_LEVELS
        tiers, for both engines at once. `gnuchess_friend_level10_limit`/
        `gnuchess_friend_level20_limit` and
        `stockfish_friend_level10_limit`/`stockfish_friend_level20_limit`
        (each optional) set one engine's budget at one tier specifically,
        and win over the generic `friend_level10_limit`/
        `friend_level20_limit` for that engine — quotas are tracked
        separately per engine (see self.friend_limits), not pooled, so an
        'api-user' side can draw on GNU Chess hints and Stockfish hints
        independently. Like the name settings, none of these are sticky:
        every new game gets the defaults unless overridden here, and
        usage always resets to zero. Returns (state_dict,
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
        if engine is not None:
            engine = self._validate_engine(engine)
        if white_engine is not None:
            white_engine = self._validate_engine(white_engine)
        if black_engine is not None:
            black_engine = self._validate_engine(black_engine)
        if white_name is not None:
            white_name = self._clean_text(white_name, NAME_MAX_LEN)
        if black_name is not None:
            black_name = self._clean_text(black_name, NAME_MAX_LEN)
        friend_overrides = {
            "gnuchess": {10: gnuchess_friend_level10_limit, 20: gnuchess_friend_level20_limit},
            "stockfish": {10: stockfish_friend_level10_limit, 20: stockfish_friend_level20_limit},
        }
        generic_friend_limits = {10: friend_level10_limit, 20: friend_level20_limit}
        for tier, value in generic_friend_limits.items():
            if value is not None:
                generic_friend_limits[tier] = self._validate_friend_limit(value, tier)
        for name in ENGINE_NAMES:
            for tier, value in friend_overrides[name].items():
                if value is not None:
                    friend_overrides[name][tier] = self._validate_friend_limit(value, tier)

        with self._lock:
            self.board = chess.Board()
            self.white_type = white
            self.black_type = black
            self.started = True
            self.result_reason = None
            self.resigned_by = None
            self.move_log = []
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
            if engine is not None:
                self.engine_names["white"] = engine
                self.engine_names["black"] = engine
            if white_engine is not None:
                self.engine_names["white"] = white_engine
            if black_engine is not None:
                self.engine_names["black"] = black_engine
            # Names never carry forward from the previous game — reset to
            # "no name" every time, then apply white_name/black_name for
            # this game only, if given.
            self.player_names = {"white": white_name, "black": black_name}
            self.friend_limits = {
                name: {
                    tier: (
                        friend_overrides[name][tier]
                        if friend_overrides[name][tier] is not None
                        else generic_friend_limits[tier]
                        if generic_friend_limits[tier] is not None
                        else DEFAULT_FRIEND_LIMITS[tier]
                    )
                    for tier in FRIEND_LEVELS
                }
                for name in ENGINE_NAMES
            }
            self.friend_used = {
                "white": {name: {10: 0, 20: 0} for name in ENGINE_NAMES},
                "black": {name: {10: 0, 20: 0} for name in ENGINE_NAMES},
            }

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

    def _validate_engine(self, engine_name):
        engine_name = (engine_name or "").strip().lower()
        if engine_name not in ENGINE_NAMES:
            raise GameError(f"'engine' must be one of: {', '.join(ENGINE_NAMES)}")
        return engine_name

    def _validate_friend_limit(self, value, tier):
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise GameError(
                f"'friend_level{tier}_limit' must be an integer between "
                f"{FRIEND_LIMIT_MIN} and {FRIEND_LIMIT_MAX}"
            )
        if not (FRIEND_LIMIT_MIN <= value <= FRIEND_LIMIT_MAX):
            raise GameError(
                f"'friend_level{tier}_limit' must be between "
                f"{FRIEND_LIMIT_MIN} and {FRIEND_LIMIT_MAX}"
            )
        return value

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

    def _friend_summary_locked(self):
        """Caller must hold self._lock. "Phone a friend" budget/usage for
        the current game, broken out per engine (see ENGINE_NAMES) since
        each engine's quota is tracked separately — see
        FRIEND_LEVELS/phone_a_friend(). Included in state() so any
        reader (an API user checking their own budget, or the board
        viewer's players bar) can see it without a dedicated endpoint."""
        def tiers(d):
            return {f"level_{tier}": d[tier] for tier in FRIEND_LEVELS}

        def side(color):
            return {
                name: {
                    "used": tiers(self.friend_used[color][name]),
                    "remaining": {
                        f"level_{tier}": max(0, self.friend_limits[name][tier] - self.friend_used[color][name][tier])
                        for tier in FRIEND_LEVELS
                    },
                }
                for name in ENGINE_NAMES
            }
        return {
            "limits": {name: tiers(self.friend_limits[name]) for name in ENGINE_NAMES},
            "white": side("white"),
            "black": side("black"),
        }

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
        """Caller must hold self._lock. Asks whichever engine is assigned
        to the color to move (self.engine_names) for its move in the
        current position and applies it, at the difficulty set for that
        color (self.engine_levels). Returns the move log entry, or None
        if the engine had no move to offer (shouldn't happen while the
        game is in progress, but handled defensively)."""
        color = "white" if self.board.turn == chess.WHITE else "black"
        engine_name = self.engine_names.get(color, DEFAULT_ENGINE)
        level = self.engine_levels.get(color, DEFAULT_LEVEL)
        engine = self._ensure_engine(engine_name)
        self._configure_engine_locked(engine, engine_name, level)
        limit = _search_limit_for(engine_name, level)
        result = engine.play(self.board, limit)
        move = result.move
        if move is None:
            return None
        san = self.board.san(move)
        uci = move.uci()
        self.board.push(move)
        # A custom name (set via set_name()) wins even for an 'engine' side;
        # otherwise fall back to the engine's display name so the viewer
        # and any chat log always have something readable to show.
        name = self.player_names.get(color) or ENGINE_DISPLAY_NAMES[engine_name]
        entry = {"ply": len(self.move_log) + 1, "color": color, "uci": uci, "san": san,
                  "by": "engine", "name": name, "ts": time.time()}
        self.move_log.append(entry)
        return entry

    # ---- public, lock-guarded API ------------------------------------------

    def is_started(self):
        with self._lock:
            return self.started

    def set_level(self, level, color=None):
        """Change an engine side's difficulty (0-20, Stockfish's native
        "Skill Level" scale — see LEVEL_MIN/MAX). `color` ('white' or
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

    def set_engine(self, engine_name, color=None):
        """Change which engine (see ENGINE_NAMES) plays an 'engine' side.
        `color` ('white' or 'black', optional) targets one side only —
        use this for an engine-vs-engine game, where the two sides can
        run different engines (e.g. GNU Chess vs. Stockfish). Omit
        `color` to set both sides at once. Takes effect starting with
        that side's next move. Can be called whether or not a game is
        currently running. Returns the updated {"white": name, "black": name}."""
        engine_name = self._validate_engine(engine_name)
        if color is not None and color not in ("white", "black"):
            raise GameError("'color' must be 'white' or 'black'")
        with self._lock:
            if color is None:
                self.engine_names["white"] = engine_name
                self.engine_names["black"] = engine_name
            else:
                self.engine_names[color] = engine_name
            self._bump_version_locked()
            return dict(self.engine_names)

    def set_name(self, color, name):
        """Set (or clear) a side's display name. `color` is 'white' or
        'black'. `name` is shown in the board viewer and stamped onto
        that side's move-log entries from then on; pass None (or an
        empty/whitespace-only string) to clear it back to showing just
        the side's type. Unlike set_level(), this is *not* sticky across
        games: it only applies to the current game (see new_game(),
        which always resets both names to unset first), and can be
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
                "engine_names": dict(self.engine_names),
                "phone_a_friend": self._friend_summary_locked(),
                "fullmove_number": board.fullmove_number,
                "halfmove_clock": board.halfmove_clock,
                "move_log": list(self.move_log),
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

    def make_move(self, move_str, chat=None, reasoning=None):
        """Submit a move for whichever side is currently to move — works
        the same whether that side is 'api-user' or 'web-user'; only
        'engine' turns are rejected here (an engine moves itself).

        `chat` (optional) is a short chat line attached to this move —
        it and this side's current display name (see set_name()) are
        stamped onto the move-log entry, so anyone who reads the game
        state after this point (in particular, the opponent's own next
        call) sees them; there is no separate delivery step, and no
        standalone/banter channel — all chat rides along with a move.

        `reasoning` (optional) is a private note about why this move was
        chosen. Unlike `chat`, it is never returned by this or any other
        method while the game is in progress — it is kept in
        self._reasoning_log, which no endpoint reads from until the game
        ends (see transcript() and that attribute's comment in __init__).

        Returns (player_move_entry, engine_entry_or_None) — the engine
        entry is set if, after this move, it becomes an 'engine' side's
        turn and that engine replies immediately."""
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
            clean_chat = self._clean_text(chat, CHAT_MAX_LEN)
            if clean_chat is not None:
                player_entry["chat"] = clean_chat
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

    def phone_a_friend(self, level, engine=None):
        """"Phone a friend": ask an engine for its recommended move in
        the current position, without submitting it. Only available to
        the side to move when that side is 'api-user' — this is a hint
        for a programmatic caller weighing a decision, not something a
        'web-user' or the 'engine' side itself needs. `level` must be
        one of FRIEND_LEVELS; each is budgeted separately, per side, per
        engine, per game (see self.friend_limits / self.friend_used, set
        at new_game() time) — GNU Chess hints and Stockfish hints draw on
        independent quotas, not a shared one, so an 'api-user' side can
        use both. `engine` (optional, one of ENGINE_NAMES) picks which
        engine to ask; defaults to DEFAULT_ENGINE if omitted. Calling
        this does not change the board, does not end your turn, and does
        not count as your move — you still need to submit a move
        yourself via make_move(), whether or not you take the
        suggestion. Returns
        {"level", "engine", "uci", "san", "color", "used", "limit", "remaining"}.
        Raises GameError if no game is in progress, it is not an
        'api-user' side's turn, `level` is not a valid tier, `engine` is
        not a valid engine name, or that side has no queries left at
        that level for that engine."""
        if level not in FRIEND_LEVELS:
            raise GameError(f"'level' must be one of: {', '.join(str(l) for l in FRIEND_LEVELS)}")
        engine_name = self._validate_engine(engine) if engine is not None else DEFAULT_ENGINE
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            mover_type = self._current_player_type()
            if mover_type != "api-user":
                raise GameError("phone-a-friend is only available to the 'api-user' side to move")

            color = "white" if self.board.turn == chess.WHITE else "black"
            used = self.friend_used[color][engine_name][level]
            limit = self.friend_limits[engine_name][level]
            if used >= limit:
                raise GameError(
                    f"phone-a-friend limit reached for {engine_name} level {level} "
                    f"({used} of {limit} used this game)"
                )

            engine_obj = self._ensure_engine(engine_name)
            self._configure_engine_locked(engine_obj, engine_name, level)
            search_limit = _search_limit_for(engine_name, level)
            # engine.play() only computes a move for the position it's
            # given — it does not push anything onto self.board itself,
            # so the game is untouched by asking for a hint.
            result = engine_obj.play(self.board, search_limit)
            move = result.move
            if move is None:
                raise GameError("the engine could not suggest a move for the current position")
            san = self.board.san(move)
            uci = move.uci()

            self.friend_used[color][engine_name][level] = used + 1
            self._bump_version_locked()

            return {
                "level": level,
                "engine": engine_name,
                "uci": uci,
                "san": san,
                "color": color,
                "used": self.friend_used[color][engine_name][level],
                "limit": limit,
                "remaining": max(0, limit - self.friend_used[color][engine_name][level]),
            }

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

    def transcript(self):
        """Build a PGN (Portable Game Notation) transcript of the game
        that just ended — the standard plain-text chess-game format;
        see the module comment above ChessGame for a link. Folds in any
        move-attached chat (make_move()'s `chat`) and any private
        `reasoning` (see that argument's docstring and
        self._reasoning_log's comment in __init__) as PGN comments on
        the move they belong to.

        Only available once the game has actually ended: `reasoning` is
        never exposed while a game is in progress, but once it's over
        there is no ongoing advantage left to protect, so it's folded
        into this one summary artifact instead of staying hidden
        forever. Raises GameError if no game has started, or the
        current game is still in progress.

        Returns the transcript as a single string (tag pairs, a blank
        line, then movetext ending in the PGN result token)."""
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; nothing to make a transcript of")
            status = self._status()
            if status not in FINISHED_STATUSES:
                raise GameError(
                    "the game must be over before a transcript is available "
                    f"(status: {status})"
                )
            move_log = list(self.move_log)
            reasoning_by_ply = {r["ply"]: r["reasoning"] for r in self._reasoning_log}
            winner = self._winner()
            white_type, black_type = self.white_type, self.black_type
            player_names = dict(self.player_names)
            engine_levels = dict(self.engine_levels)
            engine_names = dict(self.engine_names)
            created_at = self.created_at

        def side_label(color, ptype):
            if player_names.get(color):
                return player_names[color]
            if ptype == "engine":
                return ENGINE_DISPLAY_NAMES.get(engine_names.get(color), engine_names.get(color))
            return TYPE_LABELS.get(ptype, ptype)

        if winner == "white":
            result = "1-0"
        elif winner == "black":
            result = "0-1"
        elif status == "resigned":
            result = "*"  # shouldn't happen — resign() always sets a winner
        else:
            result = "1/2-1/2"

        tags = [
            ("Event", "computer-chess"),
            ("Site", "?"),
            ("Date", time.strftime("%Y.%m.%d", time.localtime(created_at or time.time()))),
            ("Round", "-"),
            ("White", side_label("white", white_type)),
            ("Black", side_label("black", black_type)),
            ("Result", result),
            ("WhiteType", white_type),
            ("BlackType", black_type),
        ]
        if white_type == "engine":
            tags.append(("WhiteEngine", engine_names.get("white", DEFAULT_ENGINE)))
            tags.append(("WhiteEngineLevel", str(engine_levels.get("white", DEFAULT_LEVEL))))
        if black_type == "engine":
            tags.append(("BlackEngine", engine_names.get("black", DEFAULT_ENGINE)))
            tags.append(("BlackEngineLevel", str(engine_levels.get("black", DEFAULT_LEVEL))))
        tags.append(("Termination", TERMINATION_LABELS.get(status, status)))

        header = "\n".join(f'[{key} "{_pgn_escape_tag(value)}"]' for key, value in tags)

        parts = []
        for entry in move_log:
            ply = entry["ply"]
            if entry["color"] == "white":
                parts.append(f"{(ply + 1) // 2}.")
            parts.append(entry["san"])
            comment_bits = []
            chat = entry.get("chat")
            if chat:
                comment_bits.append(f"Chat: {chat}")
            reasoning = reasoning_by_ply.get(ply)
            if reasoning:
                comment_bits.append(f"Reasoning: {reasoning}")
            if comment_bits:
                parts.append("{" + _pgn_escape_comment(" / ".join(comment_bits)) + "}")
        parts.append(result)

        movetext = _wrap_pgn_movetext(" ".join(parts))
        return f"{header}\n\n{movetext}\n"
