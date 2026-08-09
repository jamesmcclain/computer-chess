"""REST API (port 5003). All interaction with the game happens here.

No authentication — anyone who can reach the port can start games and
submit moves for either side. All requests/responses are JSON.
"""

from flask import Flask, Response, jsonify, request

from game import (
    DEFAULT_EVAL_QUALITY,
    DEFAULT_FRIEND_LIMITS,
    ENGINE_NAMES,
    EVAL_QUALITIES,
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
            "body": {"white": "api-user|web-user|engine", "black": "api-user|web-user|engine",
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
                     "friend_limits": "optional, object of the form "
                                       "{engine_name: {tier: limit}} — e.g. "
                                       f'{{"stockfish": {{"{FRIEND_LEVELS[0]}": 5}}}}; '
                                       f"each limit is {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX} or "
                                       f"{FRIEND_LIMIT_UNLIMITED} for unlimited; any engine/tier "
                                       "named here wins over friend_level10_limit/"
                                       "friend_level20_limit for that engine and tier only — "
                                       f"one of {', '.join(ENGINE_NAMES)}"},
            "description": "Start a new game, replacing any game already "
                            "in progress. Both sides can be 'engine'; the "
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
                            "'api-user' side may ask for over the course "
                            "of this game, for every engine at once. Each "
                            "engine's quota is tracked separately, not "
                            "pooled — 'friend_limits' sets one or more "
                            "engines' budgets at one or both tiers "
                            "specifically, and wins over the generic "
                            "fields for whichever engine/tier it names, so "
                            "an 'api-user' side can be given hints from "
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
        "GET /api/game": "Current game state (board, whose turn it is, "
                          "status, move log — including any chat attached "
                          "to a move — engine levels and engine choices, "
                          "player names, ...). This is also how to check "
                          "whose turn it is — see the 'turn' field. "
                          "'eval' is the eval bar's current assessment: "
                          "{'quality', 'pov', 'score_cp', 'mate', "
                          "'pending', 'error'} — 'score_cp' (centipawns, "
                          "positive favors White) or 'mate' (moves to "
                          "mate, positive means White mates, negative "
                          "means Black mates) is set, never both; "
                          "'pending' is true while a fresh evaluation for "
                          "the current position is still being computed "
                          "— 'score_cp'/'mate' hold the previous "
                          "position's values in the meantime, so the bar "
                          "doesn't flicker to neutral on every move (both "
                          "are null right after a new game, before the "
                          "first evaluation completes). See "
                          "GET /api/eval-qualities and "
                          "POST /api/game/eval-quality.",
        "GET /api/game/legal-moves": "Legal moves for the side to move. "
                                      "Optional query param: from=e2",
        "POST /api/game/move": {
            "body": {"move": "e2e4 (UCI) or e4 (SAN)",
                     "chat": "optional, up to 240 chars",
                     "reasoning": "optional, up to 1000 chars"},
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
                            "'reasoning' (optional) is a private note on "
                            "why you chose this move — unlike 'chat', it "
                            "is never returned by this or any other "
                            "endpoint while the game is in progress; it "
                            "is kept server-side only until the game ends "
                            "(see GET /api/game/transcript). If it "
                            "becomes the engine's turn afterward, that "
                            "engine replies immediately and its move is "
                            "returned as 'engine_move'.",
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
            "body": {"level": f"one of: {', '.join(str(l) for l in FRIEND_LEVELS)}",
                     "engine": f"optional, one of: {', '.join(ENGINE_NAMES)}, "
                               "defaults to gnuchess"},
            "description": "For the 'api-user' side to move only: ask an "
                            "engine what it would play in the current "
                            "position, without submitting that move. Does "
                            "not change the board, does not end your "
                            "turn, and is not a substitute for "
                            "POST /api/game/move — you still submit your "
                            "own move afterward, whether or not you take "
                            "the suggestion. Each of the two levels "
                            f"({FRIEND_LEVELS[0]} and {FRIEND_LEVELS[1]}) has its own budget for the game, "
                            "per engine — GNU Chess hints and Stockfish "
                            "hints draw on independent quotas, not a "
                            "shared one, so an 'api-user' side can use "
                            "both. Set at POST /api/game time (see "
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
                            "POST /api/game/move) and any private "
                            "'reasoning' recorded for it are folded in as "
                            "a PGN comment on that move — reasoning is "
                            "otherwise never returned by any endpoint, "
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
                engine_friend_limits=engine_friend_limits,
            )
        except GameError as e:
            return error(str(e))
        return jsonify(state=state, engine_move=engine_move), 201

    @app.get("/api/game")
    def get_state():
        if not game.is_started():
            return error("no game in progress; POST /api/game to start one", 404)
        return jsonify(game.state())

    @app.get("/api/game/legal-moves")
    def get_legal_moves():
        from_square = request.args.get("from")
        try:
            moves = game.legal_moves(from_square)
        except GameError as e:
            return error(str(e), 404 if "no game" in str(e) else 400)
        return jsonify(moves=moves, count=len(moves))

    @app.post("/api/game/move")
    def post_move():
        body = request.get_json(silent=True) or {}
        move_str = body.get("move")
        chat = body.get("chat")
        reasoning = body.get("reasoning")
        if not move_str:
            return error("'move' is required (UCI, e.g. 'e2e4', or SAN, e.g. 'e4')")
        try:
            player_move, engine_move = game.make_move(move_str, chat=chat, reasoning=reasoning)
        except GameError as e:
            return error(str(e))
        return jsonify(move=player_move, engine_move=engine_move, state=game.state())

    @app.post("/api/game/phone-a-friend")
    def post_phone_a_friend():
        body = request.get_json(silent=True) or {}
        level = body.get("level")
        engine_name = body.get("engine")
        if level is None:
            return error(f"'level' is required (one of: {', '.join(str(l) for l in FRIEND_LEVELS)})")
        try:
            level = int(level)
        except (TypeError, ValueError):
            return error(f"'level' must be one of: {', '.join(str(l) for l in FRIEND_LEVELS)}")
        try:
            advice = game.phone_a_friend(level, engine=engine_name)
        except GameError as e:
            return error(str(e))
        return jsonify(advice=advice, state=game.state())

    @app.get("/api/game/wait")
    def get_wait():
        color = request.args.get("color")
        timeout = request.args.get("timeout", type=float)
        try:
            state = game.wait_for_turn(color, timeout=timeout)
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.post("/api/game/resign")
    def post_resign():
        body = request.get_json(silent=True) or {}
        player = body.get("player")
        try:
            state = game.resign(player)
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.post("/api/game/abort")
    def post_abort():
        try:
            state = game.abort()
        except GameError as e:
            return error(str(e))
        return jsonify(state=state)

    @app.get("/api/game/transcript")
    def get_transcript():
        try:
            pgn = game.transcript()
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
