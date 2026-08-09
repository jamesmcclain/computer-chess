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
# per side, per engine, per game, so an API user can't just re-ask a
# level-20 friend a hundred times.
FRIEND_LEVELS = (10, 20)
DEFAULT_FRIEND_LIMITS = {10: 2, 20: 1}
FRIEND_LIMIT_MIN = 0  # 0 disables that tier entirely for the game
FRIEND_LIMIT_MAX = 50  # sane ceiling; this is a hint budget, not a real resource
FRIEND_LIMIT_UNLIMITED = -1  # sentinel: that tier has no budget cap at all

# The "eval bar": a live Stockfish assessment of who is winning the
# current position, shown as a vertical bar in the board viewer. It runs
# on its own dedicated Stockfish process (see ChessGame._ensure_eval_engine)
# — never the process(es) used to play an "engine" side or to answer
# phone_a_friend() — so it never shares a Skill Level setting or a UCI
# command queue with actual gameplay, and never slows a move down.
# `quality` trades update latency against how deep/accurate the
# assessment is; each level is a plain movetime cap in seconds. "off"
# disables the eval bar entirely (no engine call, no CPU spent) — the
# whole point of exposing this as a choice is that some people would
# rather not pay a second Stockfish process's CPU/latency cost at all.
EVAL_QUALITIES = ("off", "fast", "balanced", "deep")
EVAL_QUALITY_TIME_LIMITS = {"fast": 0.1, "balanced": 0.3, "deep": 1.0}  # seconds; "off" has no entry
EVAL_QUALITY_LABELS = {
    "off": "Off",
    "fast": "Fast",
    "balanced": "Balanced",
    "deep": "Deep",
}
EVAL_QUALITY_DESCRIPTIONS = {
    "off": "No eval bar. Stockfish does no extra work for it.",
    "fast": "Updates almost instantly. The assessment is shallow and can be noisy.",
    "balanced": "A good default: updates quickly and is accurate enough for most positions.",
    "deep": "Slower to update, especially during a fast engine-vs-engine game. The most accurate assessment.",
}
DEFAULT_EVAL_QUALITY = "balanced"

# A side is one of four types:
#   "api-user"    — moves come from the REST API (port 5003), e.g. an agent or curl.
#   "api-trainee" — exactly like "api-user" (same REST API), except every
#                   move must be preceded by a phone-a-friend call (if any
#                   budget is left — see FRIEND_LEVELS) and must carry both
#                   `tactical_reasoning` and `strategic_reasoning`. Skipping
#                   either forfeits the game on the spot — see
#                   ChessGame.make_move()'s trainee-requirements check and
#                   "forfeited" in FINISHED_STATUSES/TERMINATION_LABELS
#                   below. A training aid to force the discipline of an
#                   actual analysis process, not just "make a legal move".
#   "web-user"    — moves come from a person clicking the board in the
#                   browser viewer (port 5004).
#   "engine"      — one of ENGINE_NAMES plays this side automatically; which
#                   one is a separate, per-side choice (see engine_names).
# "api-user", "api-trainee", and "web-user" all behave identically to the
# game itself (each is just "a move shows up for this side eventually");
# the distinction only matters for display (the "by" field on a move, and
# which side the viewer's click-to-move UI lets the current browser act
# on) and, for "api-trainee" only, the extra requirements above. Both
# sides can be "engine" — the two engines then play each other
# automatically, one side tuned to each side's own difficulty level and
# engine choice (see ChessGame._start_autoplay).
PLAYER_TYPES = ("api-user", "api-trainee", "web-user", "engine")

# Player types that get to use phone-a-friend / must satisfy "api-trainee"'s
# extra requirements the same way "api-user" does — i.e. every place that
# used to check `mover_type == "api-user"` now checks
# `mover_type in API_PLAYER_TYPES`.
API_PLAYER_TYPES = ("api-user", "api-trainee")

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
# "tactical_reasoning" and "strategic_reasoning" are private notes an API
# user can attach to their own move (see make_move()) — never returned by
# any endpoint *while the game is in progress*, so each is allowed a
# little more room than a chat message. The one exception is transcript()
# (see below): once a game has ended, there is no ongoing competitive
# advantage left to protect, so a finished game's transcript folds both
# in as PGN comments.
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
    "aborted",
    "forfeited",
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
    "aborted": "aborted",
    "forfeited": "forfeit",
}

# transcript()'s PGN White/Black tags fall back to this when a side has
# no display name set (see set_name()) — a human-readable stand-in for
# the raw PLAYER_TYPES string. An "engine" side's fallback is looked up
# from ENGINE_DISPLAY_NAMES instead (see transcript()), since which
# engine it is matters more than the generic "engine" type string.
TYPE_LABELS = {"api-user": "API user", "api-trainee": "API trainee", "web-user": "Web user"}

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


def describe_eval_qualities():
    """{"qualities": [{"id", "label", "description"}, ...], "default"}
    describing the eval bar's speed/accuracy trade-off levels (see
    EVAL_QUALITIES above) — see GET /api/eval-qualities. Meant to be
    shown directly in a UI so a person can pick the trade-off that
    suits them, not just read about it in docs."""
    return {
        "qualities": [
            {"id": q, "label": EVAL_QUALITY_LABELS[q], "description": EVAL_QUALITY_DESCRIPTIONS[q]}
            for q in EVAL_QUALITIES
        ],
        "default": DEFAULT_EVAL_QUALITY,
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


def _friend_remaining(limit, used):
    """Queries left at one phone-a-friend tier: FRIEND_LIMIT_UNLIMITED
    (-1) if that tier has no cap (see _validate_friend_limit), else the
    ordinary limit-minus-used, floored at 0."""
    if limit == FRIEND_LIMIT_UNLIMITED:
        return FRIEND_LIMIT_UNLIMITED
    return max(0, limit - used)


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


def _format_eval(score_cp, mate):
    """Format one eval-bar reading (see ChessGame._eval_log) for a PGN
    comment: pawns to two decimals with an explicit sign for a normal
    score (e.g. "+0.34", "-1.20"), or "#N"/"#-N" for a forced mate in N
    from white's/black's perspective — both already in white's POV (see
    _run_eval's `score.pov(chess.WHITE)`), matching self.eval."""
    if mate is not None:
        return f"#{mate}"
    return f"{score_cp / 100:+.2f}"


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

        # Eval bar (see EVAL_QUALITIES above): its own dedicated Stockfish
        # process, its own lock (calls run in a background thread, outside
        # self._lock, so a slow evaluation never blocks a move), and a
        # generation counter so a result computed for a position that has
        # since been moved past gets discarded instead of overwriting a
        # newer one out of order.
        self._eval_engine = None  # chess.engine.SimpleEngine, lazily started; eval-bar only
        self._eval_lock = threading.Lock()  # serializes calls to _eval_engine
        self._eval_generation = 0
        self.eval_quality = DEFAULT_EVAL_QUALITY  # sticky across games, like engine_levels/engine_names
        self.eval = {"quality": self.eval_quality, "pov": "white",
                     "score_cp": None, "mate": None, "pending": False, "error": None}

        self.board = chess.Board()
        self.white_type = None       # "api-user" | "api-trainee" | "web-user" | "engine"
        self.black_type = None       # "api-user" | "api-trainee" | "web-user" | "engine"
        self.started = False
        self.result_reason = None    # set to "resigned"/"forfeited" on resignation/forfeit
        self.resigned_by = None      # "white" | "black"
        self.forfeited_by = None     # "white" | "black" — see make_move()'s trainee check
        self.move_log = []           # [{"ply","color","uci","san","by","name","chat","ts"}, ...]
        self.created_at = None
        # Ply an "api-trainee" side last called phone_a_friend() for (see
        # phone_a_friend() and make_move()'s trainee-requirements check) —
        # {"white": ply, "black": ply}, absent for a color that hasn't
        # called it yet this game. Compared against the *pending* ply
        # (len(self.move_log) + 1, i.e. the move about to be made) rather
        # than reset on every move, since phone_a_friend() itself doesn't
        # touch move_log — the pending ply is what actually advances.
        self._friend_called_for_ply = {}
        # Optional private notes an API user can attach to their own move
        # via make_move()'s `tactical_reasoning`/`strategic_reasoning`
        # arguments. Kept out of move_log and every other read endpoint
        # *while the game is in progress* — "not shared with the other
        # player" means not shared with anyone over the API, since there
        # is no authentication to tell players apart. The one exception
        # is transcript() (see below): once a game has ended, reasoning
        # is folded into that game's PGN transcript as move comments,
        # since there's no ongoing competitive edge left to protect at
        # that point. Reset every new_game().
        self._reasoning_log = []     # [{"ply","color","tactical_reasoning","strategic_reasoning","ts"}, ...]
        # History of the eval bar's own reads (see _trigger_eval_locked /
        # _run_eval below), one entry per ply that got a completed
        # analysis — unlike self.eval (the single latest read, always
        # public via state()), this is never returned by any endpoint
        # while the game is in progress. It exists purely to fold a
        # per-move evaluation into transcript() once the game ends,
        # alongside chat/reasoning. This eval is already public/
        # informational (the board viewer's eval bar runs on its own
        # engine, independent of either side), so there's no
        # confidentiality reason to withhold it mid-game the way
        # reasoning is — it's just not useful to any endpoint until
        # there's a finished move list to attach it to. A ply with a
        # superseded (see self._eval_generation) or errored analysis
        # simply has no entry here, same as a move with no chat. Reset
        # every new_game().
        self._eval_log = []          # [{"ply","score_cp","mate","pov","ts"}, ...]
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

    def _ensure_eval_engine(self):
        """Lazily start the eval bar's own dedicated Stockfish process
        (see EVAL_QUALITIES above). Caller must hold self._eval_lock, not
        self._lock — this process is never touched by _ensure_engine(),
        _play_engine_move_locked(), or phone_a_friend(), and its Skill
        Level is never changed from Stockfish's own default (maximum
        strength), so its assessment stays honest regardless of what
        difficulty either side is set to."""
        if self._eval_engine is None:
            path = _find_stockfish()
            self._eval_engine = chess.engine.SimpleEngine.popen_uci(path)
        return self._eval_engine

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
        with self._eval_lock:
            if self._eval_engine is not None:
                try:
                    self._eval_engine.quit()
                except Exception:
                    pass
                self._eval_engine = None

    # ---- game lifecycle -----------------------------------------------------

    def new_game(self, white, black, level=None, white_level=None, black_level=None,
                 engine=None, white_engine=None, black_engine=None,
                 white_name=None, black_name=None,
                 friend_level10_limit=None, friend_level20_limit=None,
                 engine_friend_limits=None):
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
        tiers, for every engine in ENGINE_NAMES at once. `engine_friend_limits`
        (optional, a dict of the form {engine_name: {tier: limit}}, using
        any subset of ENGINE_NAMES and FRIEND_LEVELS) sets one engine's
        budget at one tier specifically, and wins over the generic
        `friend_level10_limit`/`friend_level20_limit` for that engine —
        quotas are tracked separately per engine (see self.friend_limits),
        not pooled, so an 'api-user' side can draw on each engine's hints
        independently, no matter how many engines ENGINE_NAMES lists. Like
        the name settings, none of these are sticky:
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
        engine_friend_limits = engine_friend_limits or {}
        unknown_engines = set(engine_friend_limits) - set(ENGINE_NAMES)
        if unknown_engines:
            raise GameError(f"'engine_friend_limits' has unknown engine name(s): {', '.join(sorted(unknown_engines))}")
        friend_overrides = {
            name: {tier: (engine_friend_limits.get(name) or {}).get(tier) for tier in FRIEND_LEVELS}
            for name in ENGINE_NAMES
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
            self.forfeited_by = None
            self._friend_called_for_ply = {}
            self.move_log = []
            self._reasoning_log = []
            self._eval_log = []
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

            self._trigger_eval_locked(reset=True)
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
                    self._trigger_eval_locked()
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
                f"{FRIEND_LIMIT_MIN} and {FRIEND_LIMIT_MAX}, or {FRIEND_LIMIT_UNLIMITED} for unlimited"
            )
        if value == FRIEND_LIMIT_UNLIMITED:
            return value
        if not (FRIEND_LIMIT_MIN <= value <= FRIEND_LIMIT_MAX):
            raise GameError(
                f"'friend_level{tier}_limit' must be between "
                f"{FRIEND_LIMIT_MIN} and {FRIEND_LIMIT_MAX}, or {FRIEND_LIMIT_UNLIMITED} for unlimited"
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
        if self.result_reason == "forfeited":
            return "forfeited"
        if self.result_reason == "aborted":
            return "aborted"
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
        if self.result_reason == "forfeited":
            return "black" if self.forfeited_by == "white" else "white"
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
                        f"level_{tier}": _friend_remaining(
                            self.friend_limits[name][tier], self.friend_used[color][name][tier]
                        )
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

    def _friend_queries_left_locked(self, color):
        """Caller must hold self._lock. True if `color` has at least one
        phone-a-friend query left at any (engine, level) combination —
        used by make_move()'s 'api-trainee' requirements check to decide
        whether that side owed a phone_a_friend() call before this move.
        A side with every tier already exhausted (limit 0, or used up)
        owes nothing — there's nothing left to call."""
        return any(
            _friend_remaining(self.friend_limits[name][tier], self.friend_used[color][name][tier]) != 0
            for name in ENGINE_NAMES
            for tier in FRIEND_LEVELS
        )

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

    def set_eval_quality(self, quality):
        """Change the eval bar's speed/accuracy trade-off (see
        EVAL_QUALITIES above) — "off" turns the eval bar off entirely.
        Sticky across games, like set_level()/set_engine(): applies from
        now on regardless of whether a game is running, and survives to
        the next game. Immediately re-evaluates the current position (if
        any) at the new quality. Returns {"eval_quality": quality}."""
        quality = self._validate_eval_quality(quality)
        with self._lock:
            self.eval_quality = quality
            self._trigger_eval_locked()
            self._bump_version_locked()
            return {"eval_quality": self.eval_quality}

    def _validate_eval_quality(self, quality):
        quality = (quality or "").strip().lower()
        if quality not in EVAL_QUALITIES:
            raise GameError(f"'eval_quality' must be one of: {', '.join(EVAL_QUALITIES)}")
        return quality

    def _trigger_eval_locked(self, reset=False):
        """Caller must hold self._lock. (Re)computes the eval bar's
        assessment of the current position in the background, on the
        dedicated eval engine (see _ensure_eval_engine) — never the
        engine(s) used for actual gameplay, and never blocking the
        caller. Bumps self._eval_generation first; the background result
        is discarded on arrival if that counter has moved on again by
        then (a later move, a new game, or a quality change), so results
        can never apply out of order. Does nothing but reset self.eval to
        its "off"/empty shape when eval_quality is "off" or no game has
        started — no engine call, no CPU spent. Otherwise sets
        self.eval["pending"] True right away; unless `reset` (used only
        by new_game(), where the previous game's score is meaningless
        for a fresh board), the previous score_cp/mate stay in place
        while pending, so the bar holds its last position instead of
        flickering to neutral on every move.

        Once the game has actually ended (checkmate, stalemate, a draw,
        a resignation, or an abort), this does nothing at all — no new
        analysis, self.eval left exactly as it was — instead of
        analysing the now-terminal position (which has no legal moves,
        so an engine can only report a degenerate "mate in 0" there) or
        blanking the bar. The eval bar simply holds its last real read
        from just before the game ended, since that's the meaningful
        number a person watching cares about, and a checkmated position
        collapsing the bar to one color reads as the loser's read, not
        the winner's. Note this means self._eval_generation is *not*
        bumped here, unlike every other path below — a genuinely
        in-flight analysis of that last real position (started just
        before the game ended) is still exactly what should land and
        clear "pending" when it completes, not get discarded."""
        if self.eval_quality != "off" and self.started and self._status() != "in_progress":
            return
        self._eval_generation += 1
        generation = self._eval_generation
        if self.eval_quality == "off" or not self.started:
            self.eval = {"quality": self.eval_quality, "pov": "white",
                         "score_cp": None, "mate": None, "pending": False, "error": None}
            return
        if reset:
            self.eval = {"quality": self.eval_quality, "pov": "white",
                         "score_cp": None, "mate": None, "pending": True, "error": None}
        else:
            self.eval = {**self.eval, "quality": self.eval_quality, "pending": True, "error": None}
        board_copy = self.board.copy()
        quality = self.eval_quality
        ply = len(self.move_log)
        threading.Thread(
            target=self._run_eval, args=(generation, board_copy, quality, ply),
            daemon=True, name="eval-bar",
        ).start()

    def _run_eval(self, generation, board, quality, ply):
        """Runs in its own background thread (see _trigger_eval_locked).
        `board` is a private copy, so this never races the live game
        board. Talks to the dedicated eval engine under self._eval_lock
        only (never self._lock) for the duration of the actual engine
        call, so a slow evaluation can never block a move; self._lock is
        only reacquired briefly afterward, to publish the result.

        `ply` is the move-log length at the moment this analysis was
        triggered — i.e. which move's resulting position this is an eval
        of. On success, besides updating the live self.eval (the eval
        bar), this appends to self._eval_log so transcript() can later
        fold a per-move eval into the PGN. `ply` 0 (the starting
        position, before any move) and a failed analysis are both
        skipped — nothing to attach either to."""
        time_limit = EVAL_QUALITY_TIME_LIMITS[quality]
        try:
            with self._eval_lock:
                engine = self._ensure_eval_engine()
                info = engine.analyse(board, chess.engine.Limit(time=time_limit))
            score = info["score"].pov(chess.WHITE)
            mate = score.mate()
            result = {"score_cp": None if mate is not None else score.score(), "mate": mate, "error": None}
        except Exception as e:
            result = {"score_cp": None, "mate": None, "error": str(e)}
        with self._lock:
            if generation != self._eval_generation:
                return  # a newer position/quality has since superseded this result
            self.eval = {"quality": quality, "pov": "white", "pending": False, **result}
            if ply > 0 and result["error"] is None:
                self._eval_log.append({
                    "ply": ply, "score_cp": result["score_cp"], "mate": result["mate"],
                    "pov": "white", "ts": time.time(),
                })
            self._bump_version_locked()

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
                "eval": dict(self.eval),
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

    def make_move(self, move_str, chat=None, tactical_reasoning=None, strategic_reasoning=None):
        """Submit a move for whichever side is currently to move — works
        the same whether that side is 'api-user', 'api-trainee', or
        'web-user'; only 'engine' turns are rejected here (an engine
        moves itself).

        `chat` (optional) is a short chat line attached to this move —
        it and this side's current display name (see set_name()) are
        stamped onto the move-log entry, so anyone who reads the game
        state after this point (in particular, the opponent's own next
        call) sees them; there is no separate delivery step, and no
        standalone/banter channel — all chat rides along with a move.

        `tactical_reasoning` and `strategic_reasoning` (both optional
        for 'api-user'/'web-user', required for 'api-trainee' — see
        below) are private notes about why this move was chosen — the
        former for concrete, move-local calculation (captures, checks,
        threats), the latter for the longer-term plan behind it. Unlike
        `chat`, neither is ever returned by this or any other method
        while the game is in progress — both are kept in
        self._reasoning_log, which no endpoint reads from until the
        game ends (see transcript() and that attribute's comment in
        __init__).

        An 'api-trainee' side (see PLAYER_TYPES) must, for every move:
        (1) have called phone_a_friend() at least once since its last
        move, if it had any phone-a-friend budget left to call with
        (see _friend_queries_left_locked), and (2) supply both
        `tactical_reasoning` and `strategic_reasoning`. Failing either
        forfeits the game immediately — status becomes "forfeited",
        this side loses — and the submitted move is discarded entirely
        (never parsed, never applied), the same way a resignation
        doesn't submit a move. This is checked first, before the move
        string is even parsed, so a trainee can't dodge the requirement
        with an illegal or malformed move either. Returns
        {"forfeited": True, "by", "reasons", "ts"} in that case instead
        of a move entry — check for the "forfeited" key.

        Returns (player_move_entry, engine_entry_or_None) on an ordinary
        move — the engine entry is set if, after this move, it becomes
        an 'engine' side's turn and that engine replies immediately."""
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            mover_type = self._current_player_type()
            if mover_type == "engine":
                raise GameError("it is the engine's turn; wait for its move")

            color = "white" if self.board.turn == chess.WHITE else "black"
            pending_ply = len(self.move_log) + 1
            clean_tactical = self._clean_text(tactical_reasoning, REASONING_MAX_LEN)
            clean_strategic = self._clean_text(strategic_reasoning, REASONING_MAX_LEN)

            if mover_type == "api-trainee":
                violations = []
                if self._friend_queries_left_locked(color) and \
                        self._friend_called_for_ply.get(color) != pending_ply:
                    violations.append("no phone-a-friend call before this move, "
                                       "despite having queries left")
                if clean_tactical is None or clean_strategic is None:
                    violations.append("missing tactical_reasoning and/or strategic_reasoning")
                if violations:
                    self.result_reason = "forfeited"
                    self.forfeited_by = color
                    self._bump_version_locked()
                    return {"forfeited": True, "by": color, "reasons": violations,
                            "ts": time.time()}, None

            move = self._parse_move(move_str)
            san = self.board.san(move)
            uci = move.uci()
            self.board.push(move)
            ply = pending_ply
            player_entry = {"ply": ply, "color": color, "uci": uci, "san": san,
                              "by": mover_type, "name": self.player_names.get(color),
                              "ts": time.time()}
            clean_chat = self._clean_text(chat, CHAT_MAX_LEN)
            if clean_chat is not None:
                player_entry["chat"] = clean_chat
            self.move_log.append(player_entry)

            if clean_tactical is not None or clean_strategic is not None:
                self._reasoning_log.append({
                    "ply": ply, "color": color,
                    "tactical_reasoning": clean_tactical,
                    "strategic_reasoning": clean_strategic,
                    "ts": time.time(),
                })

            engine_entry = None
            if self._status() == "in_progress" and self._current_player_type() == "engine":
                engine_entry = self._play_engine_move_locked()

            self._trigger_eval_locked()
            self._bump_version_locked()
            return player_entry, engine_entry

    def phone_a_friend(self, level, engine=None):
        """"Phone a friend": ask an engine for its recommended move in
        the current position, without submitting it. Only available to
        the side to move when that side is 'api-user' or 'api-trainee'
        — this is a hint for a programmatic caller weighing a decision,
        not something a 'web-user' or the 'engine' side itself needs.
        `level` must be one of FRIEND_LEVELS; each is budgeted
        separately, per side, per engine, per game (see
        self.friend_limits / self.friend_used, set at new_game() time)
        — GNU Chess hints and Stockfish hints draw on independent
        quotas, not a shared one, so a side can use both. `engine`
        (optional, one of ENGINE_NAMES) picks which engine to ask;
        defaults to DEFAULT_ENGINE if omitted. Calling this does not
        change the board, does not end your turn, and does not count as
        your move — you still need to submit a move yourself via
        make_move(), whether or not you take the suggestion. For an
        'api-trainee' side, a successful call here (any engine, any
        level) satisfies that side's phone-a-friend requirement for the
        move about to be made — see make_move()'s trainee-requirements
        check. Returns
        {"level", "engine", "uci", "san", "color", "used", "limit", "remaining"}.
        Raises GameError if no game is in progress, it is not an
        'api-user'/'api-trainee' side's turn, `level` is not a valid
        tier, `engine` is not a valid engine name, or that side has no
        queries left at that level for that engine."""
        if level not in FRIEND_LEVELS:
            raise GameError(f"'level' must be one of: {', '.join(str(l) for l in FRIEND_LEVELS)}")
        engine_name = self._validate_engine(engine) if engine is not None else DEFAULT_ENGINE
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            mover_type = self._current_player_type()
            if mover_type not in API_PLAYER_TYPES:
                raise GameError(
                    "phone-a-friend is only available to the 'api-user'/'api-trainee' side to move"
                )

            color = "white" if self.board.turn == chess.WHITE else "black"
            used = self.friend_used[color][engine_name][level]
            limit = self.friend_limits[engine_name][level]
            if limit != FRIEND_LIMIT_UNLIMITED and used >= limit:
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
            if mover_type == "api-trainee":
                self._friend_called_for_ply[color] = len(self.move_log) + 1
            self._bump_version_locked()

            return {
                "level": level,
                "engine": engine_name,
                "uci": uci,
                "san": san,
                "color": color,
                "used": self.friend_used[color][engine_name][level],
                "limit": limit,
                "remaining": _friend_remaining(limit, self.friend_used[color][engine_name][level]),
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

    def abort(self):
        """Immediately end the current game with no winner (status
        "aborted"), regardless of player types — unlike resign(), this
        doesn't need a 'player' side to act as, so it also works for a
        game with no 'web-user'/'api-user' side at all (an engine-vs-
        engine match). Mainly for a spectator, or the board viewer's
        Restart button, to stop a running engine-vs-engine game on the
        spot instead of waiting for it to finish or letting it keep
        playing while a new game's settings are being chosen. A
        background engine-vs-engine autoplay loop (see _start_autoplay)
        checks status before every move, so it stops playing within one
        more already-in-flight move of this call. Returns the updated
        state, same shape as GET /api/game."""
        with self._lock:
            if not self.started:
                raise GameError("no game in progress; POST /api/game to start one")
            if self._status() != "in_progress":
                raise GameError(f"game is not in progress (status: {self._status()})")
            self.result_reason = "aborted"
            self._bump_version_locked()
            return self.state()

    def transcript(self):
        """Build a PGN (Portable Game Notation) transcript of the game
        that just ended — the standard plain-text chess-game format;
        see the module comment above ChessGame for a link. Folds in any
        move-attached chat (make_move()'s `chat`), any private
        `tactical_reasoning`/`strategic_reasoning` (see those arguments'
        docstring and self._reasoning_log's comment in __init__), and
        the eval bar's own per-move read (self._eval_log — see its
        comment in __init__) as PGN comments on the move they belong to.
        Unlike chat/reasoning, the eval is already public/informational
        (the board viewer's eval bar, independent of either side), so
        it's not withheld for confidentiality — it's just not attached
        anywhere until now, and a move with no completed analysis (eval
        bar off, or a result superseded before it landed) has no eval
        comment, same as one with no chat.

        Only available once the game has actually ended: reasoning is
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
            tactical_by_ply = {r["ply"]: r["tactical_reasoning"] for r in self._reasoning_log}
            strategic_by_ply = {r["ply"]: r["strategic_reasoning"] for r in self._reasoning_log}
            eval_by_ply = {r["ply"]: r for r in self._eval_log}
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
        elif status == "aborted":
            result = "*"  # PGN's "unknown/unterminated" result — no winner, not a draw
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
            tactical = tactical_by_ply.get(ply)
            if tactical:
                comment_bits.append(f"Tactical: {tactical}")
            strategic = strategic_by_ply.get(ply)
            if strategic:
                comment_bits.append(f"Strategic: {strategic}")
            eval_entry = eval_by_ply.get(ply)
            if eval_entry:
                comment_bits.append(f"Eval: {_format_eval(eval_entry['score_cp'], eval_entry['mate'])}")
            if comment_bits:
                parts.append("{" + _pgn_escape_comment(" / ".join(comment_bits)) + "}")
        parts.append(result)

        movetext = _wrap_pgn_movetext(" ".join(parts))
        return f"{header}\n\n{movetext}\n"
