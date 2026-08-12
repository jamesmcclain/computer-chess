"""REST API (port 5003). All interaction with the game happens here.

No authentication — anyone who can reach the port can start games and
submit moves for either side. All requests/responses are JSON.
"""

from flask import Flask, Response, jsonify, request

from game import (
    DEFAULT_EVAL_QUALITY,
    DEFAULT_FRIEND_EVAL_LIMIT,
    DEFAULT_FRIEND_KIND,
    DEFAULT_FRIEND_LIMITS,
    ENGINE_NAMES,
    EVAL_QUALITIES,
    FRIEND_EVAL_KEY,
    FRIEND_EVAL_TIME_LIMIT,
    FRIEND_KINDS,
    FRIEND_LEVELS,
    FRIEND_LIMIT_MAX,
    FRIEND_LIMIT_MIN,
    FRIEND_LIMIT_UNLIMITED,
    GameError,
    LEVEL_MAX,
    LEVEL_MIN,
    WAIT_DEFAULT_TIMEOUT_SECONDS,
    WAIT_MAX_TIMEOUT_SECONDS,
    describe_eval_qualities,
    describe_levels,
)

API_DOC = {
    "description": "computer-chess REST API. One game at a time; starting a "
                    "new game replaces any game already in progress. An "
                    "'engine' side can be either GNU Chess or Stockfish "
                    f"(see 'engine' below) — {', '.join(ENGINE_NAMES)} — "
                    "both equally supported everywhere an 'engine' side is.",
    "endpoints": {
        "POST /api/game": {
            "body": {"white": "api-user|api-trainee|web-user|engine|centaur",
                     "black": "api-user|api-trainee|web-user|engine|centaur",
                     "level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "white_level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "black_level": f"{LEVEL_MIN}-{LEVEL_MAX}, optional",
                     "engine": f"one of: {', '.join(ENGINE_NAMES)}, optional",
                     "white_engine": f"one of: {', '.join(ENGINE_NAMES)}, optional",
                     "black_engine": f"one of: {', '.join(ENGINE_NAMES)}, optional",
                     "white_name": "optional, up to 40 chars",
                     "black_name": "optional, up to 40 chars",
                     "friend_level10_limit": f"optional, {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX} or "
                                              f"{FRIEND_LIMIT_UNLIMITED} for unlimited, "
                                              f"default {DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[0]]}, every engine",
                     "friend_level20_limit": f"optional, {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX} or "
                                              f"{FRIEND_LIMIT_UNLIMITED} for unlimited, "
                                              f"default {DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[1]]}, every engine",
                     "friend_eval_limit": f"optional, {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX} or "
                                           f"{FRIEND_LIMIT_UNLIMITED} for unlimited, "
                                           f"default {DEFAULT_FRIEND_EVAL_LIMIT}; budget for the "
                                           "'eval' kind of phone-a-friend query, which is separate "
                                           "from the per-engine move-hint budgets above",
                     "friend_limits": "optional, object of the form "
                                       "{engine_name: {tier: limit}} — e.g. "
                                       f'{{"stockfish": {{"{FRIEND_LEVELS[0]}": 5}}}}; '
                                       f"each limit is {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX} or "
                                       f"{FRIEND_LIMIT_UNLIMITED} for unlimited; any engine/tier "
                                       "named here wins over friend_level10_limit/"
                                       "friend_level20_limit for that engine and tier only — "
                                       f"one of {', '.join(ENGINE_NAMES)}"},
            "description": "Start a new game, replacing any game already "
                            "in progress. 'api-trainee' behaves exactly "
                            "like 'api-user' (same REST calls), except "
                            "every move must be preceded by a "
                            "POST /api/game/phone-a-friend call — if that "
                            "side still has any budget left, see "
                            "'friend_level10_limit' etc. below — and must "
                            "include both 'tactical_reasoning' and "
                            "'strategic_reasoning' (see POST /api/game/move). "
                            "Skipping either forfeits the game immediately: "
                            "the submitted move is discarded, status "
                            "becomes 'forfeited', and the other side wins. "
                            "'centaur' also requires 'tactical_reasoning' "
                            "and 'strategic_reasoning' on every move, but "
                            "never moves the board directly — see "
                            "POST /api/game/suggest — a person at the "
                            "board viewer (port 5004) must accept the "
                            "suggestion or play a different move instead; "
                            "a suggestion missing either reasoning field is "
                            "just rejected (400), not a forfeit. "
                            "Both sides can be 'engine'; the "
                            "two engines then play each other, paced one "
                            "move at a time, with no further calls needed. "
                            "'level' sets the difficulty for both sides at "
                            "once; 'white_level'/'black_level' set one "
                            "side's difficulty and win over 'level' for "
                            "that side, useful for an engine-vs-engine "
                            "game where the two sides differ. 'engine' "
                            "picks which engine plays both 'engine' sides "
                            "at once; 'white_engine'/'black_engine' pick "
                            "one side's engine and win over 'engine' for "
                            "that side — use them to pit GNU Chess against "
                            "Stockfish. Any level or engine left unset "
                            "keeps whatever was last set; see "
                            "GET /api/engine-levels. 'white_name'/"
                            "'black_name' set that side's display name, "
                            "shown in the board viewer and stamped on its "
                            "move-log entries; leave unset to keep "
                            "whatever name was last set for that side (see "
                            "POST /api/game/name). 'friend_level10_limit'/"
                            "'friend_level20_limit' set this game's 'phone "
                            "a friend' budget (see "
                            "POST /api/game/phone-a-friend) — how many "
                            f"level-{FRIEND_LEVELS[0]} and level-{FRIEND_LEVELS[1]} engine hints an "
                            "'api-user'/'api-trainee'/'centaur' side may ask "
                            "for over the course of this game, for every "
                            "engine at once. Each "
                            "engine's quota is tracked separately, not "
                            "pooled — 'friend_limits' sets one or more "
                            "engines' budgets at one or both tiers "
                            "specifically, and wins over the generic "
                            "fields for whichever engine/tier it names, so "
                            "a side can be given hints from "
                            "every engine independently, no matter how "
                            "many engines this server supports. Any limit, "
                            "generic or per-engine, can be set to "
                            f"{FRIEND_LIMIT_UNLIMITED} to make that tier "
                            "unlimited for that engine (or every engine, "
                            "via the generic fields) instead of capped. "
                            "Unlike the level/name settings above, none of "
                            "these are sticky: every new game gets the "
                            "defaults shown above unless overridden here, "
                            "and usage always resets to zero. If white is "
                            "'engine' and black is not, that engine's "
                            "opening move is played immediately and "
                            "returned as 'engine_move'.",
        },
        "GET /api/game": "Current game state: position ('fen' and "
                          "'board_ascii'), whose turn it is ('turn'), "
                          "status, the single most recent move "
                          "('last_move', including any chat attached to "
                          "it), engine levels and engine choices, player "
                          "names, phone-a-friend budget, and each side's "
                          "'pending_suggestion' (null unless that side is "
                          "'centaur' and has an unplayed suggestion — see "
                          "POST /api/game/suggest). Responses "
                          "omit the 8x8 'board' grid and the full "
                          "'move_log' — 'fen' and 'last_move' carry the "
                          "same information far more compactly. Add "
                          "?verbose=1 here or on any endpoint below to get "
                          "the grid and the expanded phone-a-friend "
                          "breakdown back. See GET /api/eval-qualities and "
                          "POST /api/game/eval-quality for the eval bar's "
                          "own settings.",
        "GET /api/game/analysis": "Derived tactical facts about the "
                                   "current position, for the side to "
                                   "move (or ?color=white|black). Reports "
                                   "'hanging' material on both sides with "
                                   "its attackers and defenders, absolute "
                                   "'pins', the 'checkers' when in check, "
                                   "and your legal 'captures' and "
                                   "'checks'. All of it follows from the "
                                   "FEN, but deriving it by eye is where "
                                   "blunders come from. NOTE: 'hanging' "
                                   "counts direct attackers and defenders "
                                   "only. It is not a static exchange "
                                   "evaluation, and it does not see "
                                   "x-rays, batteries, or pinned "
                                   "defenders. Treat it as squares worth "
                                   "a second look, not a verdict.",
        "GET /api/game/legal-moves": "Legal moves for the side to move. "
                                      "Optional query params: from=e2 to "
                                      "restrict to moves leaving one "
                                      "square; format=compact (the "
                                      "default) returns 'moves' as a "
                                      "single space-separated string of "
                                      "UCI moves, format=full returns a "
                                      "list of objects with uci/san/from/"
                                      "to/promotion.",
        "GET /api/game/wait": "Block until it is your color's turn, the "
                               "game ends, or the timeout expires. Query "
                               "params: color=white|black (required), "
                               "timeout=SECONDS (optional). Returns "
                               "{'changed': true, 'state': {...}} when "
                               "there is something to act on, or the "
                               "minimal {'changed': false, 'turn': ..., "
                               "'game_over': ...} when the timeout expired "
                               "with the position unchanged — call again "
                               "in that case.",
        "GET /api/game/transcript": "PGN transcript of the finished game; "
                                     "400 while a game is in progress. "
                                     "Optional query param: include=all "
                                     "(the default) folds every move's "
                                     "chat, both reasoning fields, and the "
                                     "eval read into PGN comments; "
                                     "include=moves returns bare movetext.",
        "POST /api/game/move": {
            "body": {"move": "e2e4 (UCI) or e4 (SAN)",
                     "chat": "optional, up to 240 chars",
                     "tactical_reasoning": "optional, up to 1000 chars",
                     "strategic_reasoning": "optional, up to 1000 chars"},
            "description": "Submit a move for whichever side is currently "
                            "to move. 'chat' (optional) is a short chat "
                            "line attached to this move — it is stamped, "
                            "along with your current display name (see "
                            "POST /api/game/name), onto this move's entry "
                            "in 'move_log'. There is no separate inbox and "
                            "no standalone chat channel — all chat rides "
                            "along with a move this way: your opponent "
                            "sees it the next time they read the game "
                            "state, e.g. in the response to their own "
                            "next move, or a plain GET /api/game. "
                            "'tactical_reasoning' and 'strategic_reasoning' "
                            "(optional for 'api-user'/'web-user', required "
                            "for 'api-trainee' — see below) are private "
                            "notes on why you chose this move — the former "
                            "for concrete, move-local calculation, the "
                            "latter for your longer-term plan. Unlike "
                            "'chat', neither is ever returned by this or "
                            "any other endpoint while the game is in "
                            "progress; both are kept server-side only "
                            "until the game ends (see "
                            "GET /api/game/transcript). If it becomes the "
                            "engine's turn afterward, that engine replies "
                            "immediately and its move is returned as "
                            "'engine_move'. If the side to move is "
                            "'api-trainee' and it either skipped a required "
                            "POST /api/game/phone-a-friend call (see that "
                            "endpoint) or omitted 'tactical_reasoning'/"
                            "'strategic_reasoning', the submitted move is "
                            "discarded and the game ends on the spot: the "
                            "response is {'forfeited': true, 'by': "
                            "'white'|'black', 'reasons': [...], 'state': "
                            "...} instead of {'move', 'engine_move', "
                            "'state'} — check for 'forfeited' rather than "
                            "assuming the ordinary shape.",
        },
        "POST /api/game/suggest": {
            "body": {"move": "e2e4 (UCI) or e4 (SAN)",
                     "tactical_reasoning": "required, up to 1000 chars",
                     "strategic_reasoning": "required, up to 1000 chars",
                     "chat": "optional, up to 240 chars"},
            "description": "Suggest a move for a 'centaur' side to move — "
                            "400 if it isn't that side's turn or the side "
                            "isn't 'centaur'. Unlike POST /api/game/move, "
                            "this never touches the board: the move is only "
                            "checked for legality, then stored as this "
                            "side's pending suggestion (see "
                            "'pending_suggestion' in GET /api/game), "
                            "replacing whatever was suggested before. A "
                            "person at the board viewer (port 5004) then "
                            "either accepts it as-is or plays a different "
                            "legal move instead — POST /api/game/move "
                            "always fails for a 'centaur' side's turn, by "
                            "design. Both 'tactical_reasoning' and "
                            "'strategic_reasoning' are required; omitting "
                            "either is rejected (400, nothing stored) "
                            "rather than a forfeit, since nothing has been "
                            "committed to the board.",
        },
        "GET /api/game/wait": {
            "query": {"color": "white|black",
                      "timeout": f"seconds, optional, default {WAIT_DEFAULT_TIMEOUT_SECONDS}, "
                                 f"max {WAIT_MAX_TIMEOUT_SECONDS}"},
            "description": "Block until it is 'color's turn, the game "
                            "ends, or 'timeout' seconds pass — whichever "
                            "comes first — then return {'state': ...}. "
                            "Returns immediately if it is already that "
                            "side's turn, the game already ended, or no "
                            "game has started. A timeout looks the same "
                            "as any other return; check 'state.turn' "
                            "yourself. Lets a side wait for its opponent's "
                            "move with one call instead of polling "
                            "GET /api/game in a loop.",
        },
        "POST /api/game/phone-a-friend": {
            "body": {"kind": f"optional, one of: {', '.join(FRIEND_KINDS)}, "
                              f"defaults to {DEFAULT_FRIEND_KIND}",
                     "level": f"one of: {', '.join(str(l) for l in FRIEND_LEVELS)} "
                               "— required for kind 'move', not used by kind 'eval'",
                     "engine": f"optional, one of: {', '.join(ENGINE_NAMES)}, "
                               "defaults to gnuchess — applies to kind 'move' "
                               "only; kind 'eval' is always Stockfish"},
            "description": "For the 'api-user'/'api-trainee'/'centaur' side "
                            "to move only: ask for help with the current position "
                            "without submitting a move. Two kinds. "
                            "kind 'move' (the default) asks an "
                            "engine what it would play in the current "
                            "position, without submitting that move. "
                            "kind 'eval' asks a different question — not "
                            "what to play but who is winning and by how "
                            "much — answered by Stockfish at full "
                            f"strength ({FRIEND_EVAL_TIME_LIMIT:g}s of search, "
                            "never weakened by either side's difficulty "
                            "setting, and unaffected by whether the board "
                            "viewer's eval bar is switched on). It returns "
                            "'score_cp' (centipawns) and 'mate', both from "
                            "*white's* point of view whichever side asked, "
                            "plus 'eval' (the same reading preformatted, "
                            "e.g. '+0.34' or '#3') and 'favors' ('white', "
                            "'black' or 'equal') so the sign cannot be "
                            "misread. Its budget is tracked separately "
                            f"from the move-hint budgets, under '{FRIEND_EVAL_KEY}' "
                            "in 'phone_a_friend' — see 'friend_eval_limit' "
                            "on POST /api/game. Either kind satisfies an "
                            "'api-trainee' side's per-move phone-a-friend "
                            "requirement. Neither kind changes the board, "
                            "ends your turn, or is a substitute for "
                            "POST /api/game/move — you still submit your "
                            "own move afterward, whether or not you take "
                            "the suggestion. Each of the two levels "
                            f"({FRIEND_LEVELS[0]} and {FRIEND_LEVELS[1]}) has its own budget for the game, "
                            "per engine — GNU Chess hints and Stockfish "
                            "hints draw on independent quotas, not a "
                            "shared one, so a side can use "
                            "both. For 'api-trainee', a successful call "
                            "here at any level/engine satisfies that "
                            "side's phone-a-friend requirement for the "
                            "move it's about to submit (see "
                            "POST /api/game/move) — required before every "
                            "move for as long as any budget remains. Set "
                            "at POST /api/game time (see "
                            "'friend_level10_limit'/'friend_level20_limit' "
                            "and the per-engine 'friend_limits' field "
                            "above; defaults "
                            f"{DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[0]]} and "
                            f"{DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[1]]} respectively, per engine) "
                            "and tracked separately per side, so a "
                            "two-api-user game gives each caller their "
                            "own budget. 'engine' picks which engine to "
                            "ask. A budget set to "
                            f"{FRIEND_LIMIT_UNLIMITED} is unlimited — "
                            "'remaining' in the response, and in "
                            "'state.phone_a_friend', is also "
                            f"{FRIEND_LIMIT_UNLIMITED} in that case, and "
                            "the query never fails for running out. "
                            "Returns 400 if it is not your "
                            "turn, your side is not 'api-user', 'level' "
                            f"is not {FRIEND_LEVELS[0]} or {FRIEND_LEVELS[1]}, 'engine' is not a valid "
                            "engine name, or you have no queries left "
                            "at that level for that engine. Response: "
                            "{'advice': {'level', 'engine', 'uci', 'san', "
                            "'color', 'used', 'limit', 'remaining'}, "
                            "'state': {...}}. Current budget/usage for "
                            "both sides and both engines is also always "
                            "visible in 'state.phone_a_friend'.",
        },
        "POST /api/game/resign": {
            "body": {"player": "white|black"},
            "description": "Resign on behalf of a side, ending the game.",
        },
        "POST /api/game/abort": {
            "body": {},
            "description": "Immediately end the current game with no "
                            "winner (status 'aborted'), regardless of "
                            "player types — unlike POST /api/game/resign, "
                            "no 'player' side is needed, so this also "
                            "works for an engine-vs-engine game with no "
                            "'web-user'/'api-user' side at all. An "
                            "engine-vs-engine game's background autoplay "
                            "stops within one already-in-flight move of "
                            "this call. Returns 400 if no game has "
                            "started or the current game already ended.",
        },
        "GET /api/game/transcript": {
            "description": "Only once the game has ended (any status but "
                            "'in_progress'/'not_started'): a PGN "
                            "(Portable Game Notation) transcript of the "
                            "game — the standard plain-text chess format "
                            "read by lichess.org, chess.com, and most "
                            "chess software. Every move's 'chat' (see "
                            "POST /api/game/move), any private "
                            "'tactical_reasoning'/'strategic_reasoning' "
                            "recorded for it, and the eval bar's read of "
                            "the resulting position (if on — see "
                            "GET /api/eval-qualities) are folded in as a "
                            "PGN comment on that move — chat/reasoning "
                            "are otherwise never returned by any endpoint, "
                            "but once the game is over there's no ongoing "
                            "advantage left to protect. Returns 400 if no "
                            "game has started or the current game is "
                            "still in progress. Response is the raw PGN "
                            "text (Content-Type: application/x-chess-pgn), "
                            "not JSON, with a Content-Disposition header "
                            "so a browser downloads it as a .pgn file.",
        },
        "GET /api/engine-levels": "The shared engine difficulty scale "
                                   f"({LEVEL_MIN}=weakest..{LEVEL_MAX}=strongest) — "
                                   "Stockfish's own native 'Skill Level' — "
                                   "that both engines use, plus the list "
                                   "of valid engine names.",
        "POST /api/game/level": {
            "body": {"level": f"{LEVEL_MIN}-{LEVEL_MAX}",
                     "color": "white|black, optional"},
            "description": "Change an engine side's difficulty. Omit "
                            "'color' to set both sides at once (all that "
                            "matters when only one side is 'engine'); "
                            "pass 'color' to change one side of an "
                            "engine-vs-engine game without touching the "
                            "other. Works whether or not a game is "
                            "running, and takes effect on that side's "
                            "next move. Returns the updated "
                            "{'white': N, 'black': N}.",
        },
        "POST /api/game/engine": {
            "body": {"engine": f"one of: {', '.join(ENGINE_NAMES)}",
                     "color": "white|black, optional"},
            "description": "Change which engine plays an 'engine' side. "
                            "Omit 'color' to set both sides at once; pass "
                            "'color' to change one side of an "
                            "engine-vs-engine game without touching the "
                            "other — use this to pit GNU Chess against "
                            "Stockfish. Works whether or not a game is "
                            "running, and takes effect on that side's "
                            "next move. Returns the updated "
                            "{'white': name, 'black': name}.",
        },
        "POST /api/game/name": {
            "body": {"color": "white|black", "name": "up to 40 chars"},
            "description": "Set (or, with an empty 'name', clear) one "
                            "side's display name. Shown in the board "
                            "viewer and stamped on that side's move-log "
                            "entries from then on. Useful for an API user "
                            "joining a game they did not start, since "
                            "'white_name'/'black_name' on POST /api/game "
                            "only set a name when that game is created. "
                            "Works whether or not a game is running. "
                            "Returns the updated "
                            "{'white': name_or_null, 'black': name_or_null}.",
        },
        "GET /api/eval-qualities": "The eval bar's speed/accuracy "
                                    "trade-off levels — each entry has "
                                    "'id', 'label', and a plain-language "
                                    "'description' of the trade-off, "
                                    "meant to be shown directly in a UI. "
                                    f"Default: '{DEFAULT_EVAL_QUALITY}'.",
        "POST /api/game/eval-quality": {
            "body": {"quality": f"one of: {', '.join(EVAL_QUALITIES)}"},
            "description": "Change the eval bar's speed/accuracy "
                            "trade-off (see GET /api/eval-qualities for "
                            "what each level means). 'off' turns the eval "
                            "bar off entirely — no extra Stockfish work "
                            "is done. Sticky, like 'POST /api/game/level'/"
                            "'POST /api/game/engine': applies from now on "
                            "regardless of whether a game is running, and "
                            "survives to the next game. The eval bar runs "
                            "on its own dedicated Stockfish process, "
                            "always at full strength, entirely separate "
                            "from any engine playing a side or answering "
                            "a phone-a-friend query — it is never affected "
                            "by, and never affects, either. Returns "
                            "{'eval_quality': quality}.",
        },
    },
    "viewer": "A board viewer is served separately on port 5004. It shows "
              "the game live and also lets a person start a game or play "
              "as 'web-user' by clicking the board — see that page.",
}


# How much of the game state each kind of API response carries. Every
# byte here is paid for twice — once on the wire, and again in the
# context window of the agent reading it — so the default is the least
# that still answers the question, and `?verbose=1` restores the rest.
#
# The board grid is dropped everywhere: it is the single largest field
# in the state, and it re-encodes a position already carried by 'fen'
# and 'board_ascii' in a fraction of the space. It exists for the board
# viewer's per-square rendering, which does not go through this app.
#
# LEAN_STATE also drops the fields that cannot change during a game
# ('players', 'player_names', 'engine_levels', 'engine_names',
# 'started'); repeating them on every move is pure noise. CONTEXT_STATE
# keeps them, and is used by the two calls whose job is to tell a caller
# where things stand: POST /api/game and GET /api/game.
#
# 'phone_a_friend' is never dropped by either — an 'api-trainee' side
# must be able to read its own remaining budget before every move — but
# both send the compact form; see _friend_summary_compact_locked().
LEAN_STATE = {
    "include_log": False,
    "include_board_grid": False,
    "include_static": False,
    "friend_detail": "compact",
}
CONTEXT_STATE = {**LEAN_STATE, "include_static": True}
VERBOSE_STATE = {
    "include_log": False,
    "include_board_grid": True,
    "include_static": True,
    "friend_detail": "full",
}


def _state_opts(base):
    """Pick the state() options for this request: `base` normally, or the
    full pre-trimming payload when the caller passes ?verbose=1. The
    escape hatch is there so a caller that genuinely wants the board grid
    or the full phone-a-friend breakdown can still get them without a
    second round trip."""
    verbose = (request.args.get("verbose") or "").strip().lower()
    return VERBOSE_STATE if verbose in ("1", "true", "yes") else base


def create_api_app(game):
    app = Flask(__name__)

    def error(message, status=400):
        return jsonify(error=message), status

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error="not found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(error="method not allowed"), 405

    @app.get("/api")
    @app.get("/api/")
    def index():
        return jsonify(API_DOC)

    @app.post("/api/game")
    def new_game():
        body = request.get_json(silent=True) or {}
        white = body.get("white", "api-user")
        black = body.get("black", "engine")
        level = body.get("level")
        white_level = body.get("white_level")
        black_level = body.get("black_level")
        engine = body.get("engine")
        white_engine = body.get("white_engine")
        black_engine = body.get("black_engine")
        white_name = body.get("white_name")
        black_name = body.get("black_name")
        friend_level10_limit = body.get("friend_level10_limit")
        friend_level20_limit = body.get("friend_level20_limit")
        friend_eval_limit = body.get("friend_eval_limit")
        friend_limits = body.get("friend_limits")
        try:
            engine_friend_limits = None
            if isinstance(friend_limits, dict):
                engine_friend_limits = {
                    name: {int(tier): limit for tier, limit in (tiers or {}).items()}
                    for name, tiers in friend_limits.items()
                }
        except (TypeError, ValueError, AttributeError):
            return error("'friend_limits' must be an object of the form "
                          "{engine_name: {tier: limit}}")
        try:
            state, engine_move = game.new_game(
                white, black, level=level, white_level=white_level, black_level=black_level,
                engine=engine, white_engine=white_engine, black_engine=black_engine,
                white_name=white_name, black_name=black_name,
                friend_level10_limit=friend_level10_limit, friend_level20_limit=friend_level20_limit,
                friend_eval_limit=friend_eval_limit,
                engine_friend_limits=engine_friend_limits,
                state_opts=_state_opts(CONTEXT_STATE),
            )
        except GameError as e:
            return error(str(e))
        return jsonify(state=state, engine_move=engine_move), 201

    @app.get("/api/game")
    def get_state():
        if not game.is_started():
            return error("no game in progress; POST /api/game to start one", 404)
        return jsonify(game.state(**_state_opts(CONTEXT_STATE)))

    @app.get("/api/game/analysis")
    def get_analysis():
        color = request.args.get("color")
        try:
            return jsonify(game.analysis(color))
        except GameError as e:
            return error(str(e), 404 if "no game" in str(e) else 400)

    @app.get("/api/game/legal-moves")
    def get_legal_moves():
        from_square = request.args.get("from")
        fmt = (request.args.get("format") or "compact").strip().lower()
        if fmt not in ("compact", "full"):
            return error("'format' must be 'compact' or 'full'")
        try:
            moves = game.legal_moves(from_square)
        except GameError as e:
            return error(str(e), 404 if "no game" in str(e) else 400)
        if fmt == "compact":
            # 'from', 'to' and 'promotion' are all substrings of 'uci', so
            # the full form spends roughly ten bytes restating each move
            # for every one it takes to state it. The compact form is a
            # single space-separated string of UCI moves — around a
            # thirteenth of the size, and directly usable as the 'move'
            # field of POST /api/game/move.
            return jsonify(moves=" ".join(m["uci"] for m in moves), count=len(moves))
        return jsonify(moves=moves, count=len(moves))

    @app.post("/api/game/move")
    def post_move():
        body = request.get_json(silent=True) or {}
        move_str = body.get("move")
        chat = body.get("chat")
        tactical_reasoning = body.get("tactical_reasoning")
        strategic_reasoning = body.get("strategic_reasoning")
        if not move_str:
            return error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        try:
            player_move, engine_move = game.make_move(
                move_str, chat=chat,
                tactical_reasoning=tactical_reasoning,
                strategic_reasoning=strategic_reasoning,
                source="api",
            )
        except GameError as e:
            return error(str(e))
        opts = _state_opts(LEAN_STATE)
        if player_move.get("forfeited"):
            return jsonify(forfeited=True, by=player_move["by"],
                            reasons=player_move["reasons"], state=game.state(**opts))
        return jsonify(move=player_move, engine_move=engine_move, state=game.state(**opts))

    @app.post("/api/game/suggest")
    def post_suggest():
        body = request.get_json(silent=True) or {}
        move_str = body.get("move")
        tactical_reasoning = body.get("tactical_reasoning")
        strategic_reasoning = body.get("strategic_reasoning")
        chat = body.get("chat")
        if not move_str:
            return error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        if not tactical_reasoning or not strategic_reasoning:
            return error("'tactical_reasoning' and 'strategic_reasoning' are both required")
        try:
            suggestion = game.suggest_move(
                move_str, tactical_reasoning, strategic_reasoning, chat=chat,
            )
        except GameError as e:
            return error(str(e), 404 if "no game" in str(e) else 400)
        return jsonify(suggestion=suggestion, state=game.state(**_state_opts(LEAN_STATE)))

    @app.post("/api/game/phone-a-friend")
    def post_phone_a_friend():
        body = request.get_json(silent=True) or {}
        kind = body.get("kind") or DEFAULT_FRIEND_KIND
        level = body.get("level")
        engine_name = body.get("engine")
        if kind not in FRIEND_KINDS:
            return error(f"'kind' must be one of: {', '.join(FRIEND_KINDS)}")
        # 'level' picks a tier of move hint, so it is required for a
        # 'move' query and meaningless for an 'eval' one, which has no
        # tiers and only ever asks Stockfish at full strength.
        if kind == "move":
            if level is None:
                return error(f"'level' is required (one of: {', '.join(str(l) for l in FRIEND_LEVELS)})")
            try:
                level = int(level)
            except (TypeError, ValueError):
                return error(f"'level' must be one of: {', '.join(str(l) for l in FRIEND_LEVELS)}")
        try:
            advice = game.phone_a_friend(level, engine=engine_name, kind=kind)
        except GameError as e:
            return error(str(e))
        return jsonify(advice=advice, state=game.state(**_state_opts(LEAN_STATE)))

    @app.get("/api/game/wait")
    def get_wait():
        color = request.args.get("color")
        timeout = request.args.get("timeout", type=float)
        try:
            state, ready = game.wait_for_turn(
                color, timeout=timeout, state_opts=_state_opts(LEAN_STATE)
            )
        except GameError as e:
            return error(str(e))
        if not ready:
            # The wait simply expired with the position unchanged. Sending
            # a full state to say "nothing happened" is the most wasteful
            # response this API can produce — a caller waiting on a slow
            # human opponent may collect several of these in a row — so
            # send just enough to confirm that and let them call again.
            return jsonify(changed=False, turn=state["turn"], game_over=state["game_over"])
        return jsonify(changed=True, state=state)

    @app.post("/api/game/resign")
    def post_resign():
        body = request.get_json(silent=True) or {}
        player = body.get("player")
        try:
            state = game.resign(player, state_opts=_state_opts(LEAN_STATE))
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.post("/api/game/abort")
    def post_abort():
        try:
            state = game.abort(state_opts=_state_opts(LEAN_STATE))
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.get("/api/game/transcript")
    def get_transcript():
        include = (request.args.get("include") or "all").strip().lower()
        if include not in ("all", "moves"):
            return error("'include' must be 'all' or 'moves'")
        try:
            # Defaults to the fully annotated transcript: the reasoning is
            # withheld for the whole game and folded in here, so quietly
            # dropping it would defeat the point of collecting it.
            # ?include=moves is the valve for a caller that wants only the
            # movetext. The viewer's own download is always fully annotated.
            pgn = game.transcript(include_annotations=(include == "all"))
        except GameError as e:
            return error(str(e))
        return Response(
            pgn,
            mimetype="application/x-chess-pgn",
            headers={"Content-Disposition": 'attachment; filename="computer-chess.pgn"'},
        )

    @app.get("/api/engine-levels")
    def get_engine_levels():
        return jsonify(describe_levels())

    @app.get("/api/eval-qualities")
    def get_eval_qualities():
        return jsonify(describe_eval_qualities())

    @app.post("/api/game/eval-quality")
    def post_eval_quality():
        body = request.get_json(silent=True) or {}
        quality = body.get("quality")
        if not quality:
            return error(f"'quality' is required (one of: {', '.join(EVAL_QUALITIES)})")
        try:
            result = game.set_eval_quality(quality)
        except GameError as e:
            return error(str(e))
        return jsonify(result)

    @app.post("/api/game/level")
    def post_level():
        body = request.get_json(silent=True) or {}
        level = body.get("level")
        color = body.get("color")
        if level is None:
            return error(f"'level' is required ({LEVEL_MIN}-{LEVEL_MAX})")
        try:
            new_levels = game.set_level(level, color=color)
        except GameError as e:
            return error(str(e))
        return jsonify(levels=new_levels)

    @app.post("/api/game/engine")
    def post_engine():
        body = request.get_json(silent=True) or {}
        engine_name = body.get("engine")
        color = body.get("color")
        if not engine_name:
            return error(f"'engine' is required (one of: {', '.join(ENGINE_NAMES)})")
        try:
            new_engines = game.set_engine(engine_name, color=color)
        except GameError as e:
            return error(str(e))
        return jsonify(engines=new_engines)

    @app.post("/api/game/name")
    def post_name():
        body = request.get_json(silent=True) or {}
        color = body.get("color")
        name = body.get("name")
        try:
            new_names = game.set_name(color, name)
        except GameError as e:
            return error(str(e))
        return jsonify(player_names=new_names)

    return app
