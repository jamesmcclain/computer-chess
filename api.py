"""REST API (port 5003). All interaction with the game happens here.

No authentication — anyone who can reach the port can start games and
submit moves for either side. All requests/responses are JSON.
"""

from flask import Flask, Response, jsonify, request

from game import (
    DEFAULT_FRIEND_LIMITS,
    ENGINE_NAMES,
    FRIEND_LEVELS,
    FRIEND_LIMIT_MAX,
    FRIEND_LIMIT_MIN,
    GameError,
    LEVEL_MAX,
    LEVEL_MIN,
    WAIT_DEFAULT_TIMEOUT_SECONDS,
    WAIT_MAX_TIMEOUT_SECONDS,
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
                     "friend_level5_limit": f"optional, {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX}, "
                                             f"default {DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[0]]}",
                     "friend_level10_limit": f"optional, {FRIEND_LIMIT_MIN}-{FRIEND_LIMIT_MAX}, "
                                              f"default {DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[1]]}"},
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
                            "POST /api/game/name). 'friend_level5_limit'/"
                            "'friend_level10_limit' set this game's 'phone "
                            "a friend' budget (see "
                            "POST /api/game/phone-a-friend) — how many "
                            f"level-{FRIEND_LEVELS[0]} and level-{FRIEND_LEVELS[1]} engine hints an "
                            "'api-user' side may ask for over the course "
                            "of this game. Unlike the level/name settings "
                            "above, these are not sticky: every new game "
                            "gets the defaults shown above unless "
                            "overridden here, and usage always resets to "
                            "zero. If white is 'engine' and black is not, "
                            "that engine's opening move is played "
                            "immediately and returned as 'engine_move'.",
        },
        "GET /api/game": "Current game state (board, whose turn it is, "
                          "status, move log — including any chat attached "
                          "to a move — engine levels and engine choices, "
                          "player names, ...). This is also how to check "
                          "whose turn it is — see the 'turn' field.",
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
                            "set at POST /api/game time (see "
                            "'friend_level5_limit'/'friend_level10_limit' "
                            "above; defaults "
                            f"{DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[0]]} and "
                            f"{DEFAULT_FRIEND_LIMITS[FRIEND_LEVELS[1]]} respectively) "
                            "and tracked separately per side, so a "
                            "two-api-user game gives each caller their "
                            "own budget. 'engine' picks which engine to "
                            "ask. Returns 400 if it is not your "
                            "turn, your side is not 'api-user', 'level' "
                            f"is not {FRIEND_LEVELS[0]} or {FRIEND_LEVELS[1]}, 'engine' is not a valid "
                            "engine name, or you have no queries left "
                            "at that level. Response: {'advice': "
                            "{'level', 'engine', 'uci', 'san', 'color', "
                            "'used', 'limit', 'remaining'}, 'state': {...}}. "
                            "Current budget/usage for both sides is also "
                            "always visible in 'state.phone_a_friend'.",
        },
        "POST /api/game/resign": {
            "body": {"player": "white|black"},
            "description": "Resign on behalf of a side, ending the game.",
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
        friend_level5_limit = body.get("friend_level5_limit")
        friend_level10_limit = body.get("friend_level10_limit")
        try:
            state, engine_move = game.new_game(
                white, black, level=level, white_level=white_level, black_level=black_level,
                engine=engine, white_engine=white_engine, black_engine=black_engine,
                white_name=white_name, black_name=black_name,
                friend_level5_limit=friend_level5_limit, friend_level10_limit=friend_level10_limit,
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
