#!/usr/bin/env python3
"""Command-line wrapper for the computer-chess REST API.

WHY THIS EXISTS
---------------
Calling the API with raw curl puts the whole JSON response into the
caller's context, every turn, for the whole game. This script makes the
same calls and prints a short digest instead. Two effects:

1. Only what this script prints costs context. Calls that this script
   makes for its own checks cost network time, but no tokens. The
   `--side` verification below is free for that reason.
2. One `turn` call replaces a state fetch and a legal-move fetch.

WHAT THIS SCRIPT WILL NEVER DO
------------------------------
There is no `play` subcommand, and there must never be one. The skill
bans a code loop that submits moves, because the loop removes the think
step between the board and the move. A subcommand that played more than
one move would obey the letter of that ban and defeat its purpose.

`turn` reads. `move` writes. They stay separate commands so that a
decision happens between them. Add new read commands freely. Do not add
a command that reads a position and moves in the same run.

(The `phone-a-friend` command does accept more than one query in a run.
That is not a move loop: no query changes the board or ends the turn.)

USAGE
-----
    chess.py new --white api-user --black engine --level 10
    chess.py join --side white --name "Deep Purple"
    chess.py turn --side white
    chess.py phone-a-friend --side white eval 20:stockfish
    chess.py move --side white e2e4 --chat "..." --tactical "..." --strategic "..."
    chess.py wait --side white
    chess.py set --side white --name "Deep Purple"
    chess.py resign --side white
    chess.py transcript --out game.pgn

The API base URL comes from --url, else the CHESS_API environment
variable, else the default below.

Exit status: 0 on success, 1 on an API or usage error reported by the
server, 2 on a bad command line.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://10.0.2.2:5003"
TIMEOUT_SECONDS = 70  # above the server's own 55s cap on GET /api/game/wait


class ApiError(Exception):
    """An error the server reported, or a failure to reach it."""


# ---- transport ------------------------------------------------------------

def call(base, method, path, body=None, raw=False):
    """Make one API call. Returns the parsed JSON body, or the raw text
    when `raw`. Raises ApiError with the server's own message on a 4xx,
    so every command reports the same way."""
    url = base.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            text = response.read().decode()
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            message = json.loads(text).get("error", text)
        except ValueError:
            message = text
        raise ApiError(f"{e.code}: {message}")
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {url} ({e.reason}). Is the container running?")
    if raw:
        return text
    return json.loads(text)


# ---- formatting -----------------------------------------------------------

def board_lines(board_ascii):
    """Rank-and-file labels around the server's ASCII board. The labels
    matter: they remove the need to count rows to name a square."""
    rows = board_ascii.splitlines()
    lines = [f"  {8 - i} {row}" for i, row in enumerate(rows)]
    lines.append("    a b c d e f g h")
    return lines


def friend_line(state, side):
    """Print every remaining hint budget in plain language.

    The compact API form stores engine tiers as ``level_10/level_20``.
    Expand it here so an agent cannot confuse GNU Chess with Stockfish or
    level 10 with level 20. ``-1`` means unlimited.
    """
    budget = (state.get("phone_a_friend") or {}).get(side)
    if not budget:
        return None

    def value(engine, tier):
        raw = budget.get(engine, "0/0")
        if tier == "eval":
            return raw
        values = str(raw).split("/", 1)
        return values[0 if tier == "10" else 1] if len(values) == 2 else "0"

    def pretty(raw):
        return "unlimited" if str(raw) == "-1" else str(raw)

    return ("budget remaining: "
            f"L10 GNU Chess {pretty(value('gnuchess', '10'))}; "
            f"L20 GNU Chess {pretty(value('gnuchess', '20'))}; "
            f"L10 Stockfish {pretty(value('stockfish', '10'))}; "
            f"L20 Stockfish {pretty(value('stockfish', '20'))}; "
            f"Stockfish Eval {pretty(value('stockfish_eval', 'eval'))}")


def tactics_lines(report):
    """The derived tactical facts from GET /api/game/analysis, as short
    lines. These are the facts a caller most often misses when reading a
    board: loose material, pins, and the checks and captures available.

    The `hanging` entries are a one-ply heuristic from the server, not a
    full exchange evaluation. They mark squares worth a second look."""
    lines = []
    if report["in_check"]:
        lines.append("  IN CHECK from " + ", ".join(report["checkers"]))
    for scope, label in (("yours", "YOURS "), ("theirs", "theirs")):
        for item in report["hanging"][scope]:
            defenders = ",".join(item["defenders"]) or "none"
            lines.append(
                f"  {label} {item['piece']}{item['square']} {item['risk']}"
                f" (attackers {','.join(item['attackers'])}; defenders {defenders})"
            )
    for pin in report["pins"]:
        lines.append(f"  pin: {pin['color']} {pin['piece']}{pin['square']} cannot move off the king's line")
    if report["checks"]:
        lines.append("  checks you can give: " + " ".join(report["checks"]))
    if report["captures"]:
        lines.append("  captures you can make: " + " ".join(report["captures"]))
    return lines


def describe_move(entry):
    """One move-log entry as 'white e4 (e2e4)', with chat if present."""
    if not entry:
        return None
    text = f"{entry['color']} {entry.get('san') or entry['uci']} ({entry['uci']})"
    who = entry.get("name") or entry.get("by")
    if who:
        text += f" by {who}"
    if entry.get("chat"):
        text += f'  "{entry["chat"]}"'
    return text


def status_line(state, side=None):
    """The one line every command prints: whose turn, how far in, and
    whether the game is still running."""
    bits = [f"status: {state['status']}"]
    if state.get("game_over"):
        bits.append(f"winner: {state.get('winner') or 'none (draw/abort)'}")
    else:
        turn = state["turn"]
        bits.append(f"turn: {turn}")
        if side:
            bits.append("YOUR MOVE" if turn == side else f"waiting on {turn}")
    bits.append(f"move {state['fullmove_number']}")
    if state.get("in_check"):
        bits.append("CHECK")
    return "  ".join(bits)


# ---- side checking --------------------------------------------------------

def verify_side(base, side, state=None):
    """Confirm it is `side`'s turn before acting for that side, and
    return the state.

    This is why --side is required on the commands that act. The API has
    no authentication: in a game where both sides are 'api-user', a move
    submitted on the wrong side is accepted and applied. The server
    cannot catch that mistake, so it is caught here.

    The state fetched here is not printed, so this check costs a request
    but no context."""
    if state is None:
        state = call(base, "GET", "/api/game")
    if state.get("game_over"):
        raise ApiError(
            f"the game has already ended (status: {state['status']}). No move to make."
        )
    if state["turn"] != side:
        raise ApiError(
            f"it is {state['turn']}'s turn, not {side}'s. "
            f"Refusing to act as {side}. Use 'wait --side {side}' first."
        )
    return state


# ---- commands -------------------------------------------------------------

def cmd_new(args):
    body = {"white": args.white, "black": args.black}
    for name in ("level", "white_level", "black_level", "engine", "white_engine",
                 "black_engine", "white_name", "black_name", "friend_level10_limit",
                 "friend_level20_limit", "friend_eval_limit"):
        value = getattr(args, name, None)
        if value is not None:
            body[name] = value
    result = call(args.url, "POST", "/api/game", body)
    state = result["state"]
    print(f"new game: white={state['players']['white']}  black={state['players']['black']}")
    levels, engines = state.get("engine_levels", {}), state.get("engine_names", {})
    for color in ("white", "black"):
        if state["players"][color] == "engine":
            print(f"  {color}: {engines.get(color)} level {levels.get(color)}")
    if result.get("engine_move"):
        print("engine opened: " + describe_move(result["engine_move"]))
    print(status_line(state))
    line = friend_line(state, "white")
    if line:
        print(line + "   (per side)")
    return 0


def cmd_turn(args):
    """Read-only. Combines the state and the legal-move list, which are
    otherwise two calls every turn."""
    state = call(args.url, "GET", "/api/game")
    print(status_line(state, args.side))
    last = describe_move(state.get("last_move"))
    if last:
        print("last: " + last)
    if state.get("game_over"):
        print("The game is over. Do not submit a move.")
        return 0
    if not args.brief:
        for line in board_lines(state["board_ascii"]):
            print(line)
        print("fen: " + state["fen"])
    if not args.brief:
        report = call(args.url, "GET", "/api/game/analysis")
        lines = tactics_lines(report)
        if lines:
            print("tactics:")
            for line in lines:
                print(line)
    line = friend_line(state, args.side) if args.side else None
    if line:
        print(line)

    moves = call(args.url, "GET", "/api/game/legal-moves")
    count = moves["count"]
    # The full list is fetched either way — fetching costs a request, not
    # context. Printing it is what costs context, so print it only when
    # it earns the space: when the choice is small enough to read, or
    # when check makes every legal move critical. Otherwise print the
    # count. `move` validates against this same list before it submits,
    # so a caller that guesses wrong is corrected without losing a turn.
    if args.legal or count <= 10 or state.get("in_check"):
        print(f"legal ({count}): {moves['moves']}")
    else:
        print(f"legal moves: {count} (run with --legal to list them)")
    return 0


def cmd_move(args):
    verify_side(args.url, args.side)
    # Check the move against the legal list before submitting it. This
    # call costs a request but no context, and it turns an illegal move
    # from a rejected API call into a corrected one, with the legal list
    # printed exactly when it is needed.
    legal = call(args.url, "GET", "/api/game/legal-moves")
    if args.move not in legal["moves"].split():
        raise ApiError(
            f"{args.move!r} is not legal in this position. "
            f"Legal moves ({legal['count']}): {legal['moves']}"
        )
    body = {
        "move": args.move,
        "chat": args.chat,
        "tactical_reasoning": args.tactical,
        "strategic_reasoning": args.strategic,
    }
    result = call(args.url, "POST", "/api/game/move", body)
    if result.get("forfeited"):
        # Only an 'api-trainee' side can reach this. The move was thrown
        # away and the game is over; say so plainly rather than let it
        # look like an ordinary move.
        print(f"FORFEITED by {result['by']} — the move was NOT applied.")
        for reason in result["reasons"]:
            print(f"  - {reason}")
        print(status_line(result["state"]))
        return 1
    print("played: " + describe_move(result["move"]))
    if result.get("engine_move"):
        print("reply:  " + describe_move(result["engine_move"]))
    print(status_line(result["state"], args.side))
    return 0


def cmd_suggest(args):
    """Suggest a move for a 'centaur' side. This never plays the move —
    it only proposes it. A person at the board viewer decides whether to
    accept it or play something else instead; there is no way to force it
    onto the board from here."""
    verify_side(args.url, args.side)
    legal = call(args.url, "GET", "/api/game/legal-moves")
    if args.move not in legal["moves"].split():
        raise ApiError(
            f"{args.move!r} is not legal in this position. "
            f"Legal moves ({legal['count']}): {legal['moves']}"
        )
    body = {
        "move": args.move,
        "chat": args.chat,
        "tactical_reasoning": args.tactical,
        "strategic_reasoning": args.strategic,
    }
    result = call(args.url, "POST", "/api/game/suggest", body)
    suggestion = result["suggestion"]
    print(f"suggested: {suggestion['san']} ({suggestion['uci']}) — NOT played. "
          "Waiting for a person at the board to accept it or play a different move.")
    print(status_line(result["state"], args.side))
    return 0


def parse_query(spec):
    """Turn one phone-a-friend query spec into a request body.

    'eval'            -> the full-strength Stockfish position assessment
    '20'              -> a level-20 move hint from the default engine
    '10:stockfish'    -> a level-10 move hint from a named engine
    """
    if spec == "eval":
        return {"kind": "eval"}
    level, _, engine = spec.partition(":")
    if level not in ("10", "20"):
        raise ApiError(
            f"bad query {spec!r}. Use 'eval', '10', '20', or LEVEL:ENGINE such as '20:stockfish'."
        )
    body = {"kind": "move", "level": int(level)}
    if engine:
        body["engine"] = engine
    return body


def cmd_phone_a_friend(args):
    """One or more queries in a single run. No query changes the board,
    so asking several in a row is safe — see the module docstring on why
    this is not a move loop."""
    state = verify_side(args.url, args.side)
    failures = 0
    for spec in args.queries:
        try:
            body = parse_query(spec)
            advice = call(args.url, "POST", "/api/game/phone-a-friend", body)["advice"]
        except ApiError as e:
            print(f"{spec}: FAILED — {e}", file=sys.stderr)
            failures += 1
            continue
        left = "unlimited" if advice["remaining"] == -1 else f"{advice['remaining']} left"
        if advice["kind"] == "eval":
            # score_cp and mate are white's point of view whatever side
            # asked; 'favors' is the unambiguous reading.
            print(f"eval  stockfish -> {advice['eval']} (favors {advice['favors']})  [{left}]")
        else:
            print(f"hint  {advice['engine']} L{advice['level']} -> "
                  f"{advice['san']} ({advice['uci']})  [{left}]")
        state = None
    if state is None:
        fresh = call(args.url, "GET", "/api/game")
        line = friend_line(fresh, args.side)
        if line:
            print(line)
    return 1 if failures else 0


def cmd_wait(args):
    result = call(args.url, "GET", f"/api/game/wait?color={args.side}&timeout={args.timeout}")
    if not result.get("changed"):
        print(f"no change after {args.timeout}s — still {result['turn']}'s turn. Call again.")
        return 0
    state = result["state"]
    print(status_line(state, args.side))
    last = describe_move(state.get("last_move"))
    if last:
        print("last: " + last)
    return 0


def cmd_resign(args):
    """Ends the game. CAUTION: this cannot be undone. --abort ends the
    game with no winner, where a resignation gives the win away."""
    if args.abort:
        state = call(args.url, "POST", "/api/game/abort")["state"]
    elif args.side:
        state = call(args.url, "POST", "/api/game/resign", {"player": args.side})["state"]
    else:
        raise ApiError("give --side to resign for a color, or --abort to end with no winner")
    print(status_line(state))
    return 0


def cmd_join(args):
    """Take over a side of a game that is already running.

    There is no login and no seat reservation, so joining is a check
    rather than a claim: confirm that a game exists, that the side is one
    an API caller may play, and that the game has not ended. After that,
    play it by passing the same --side to every other command."""
    state = call(args.url, "GET", "/api/game")
    players = state.get("players") or {}
    kind = players.get(args.side)
    other = players.get("white" if args.side == "black" else "black")
    if kind not in ("api-user", "api-trainee", "centaur"):
        raise ApiError(
            f"the {args.side} side is {kind!r}, which you cannot take over. "
            f"Only 'api-user', 'api-trainee', and 'centaur' sides are joinable. "
            f"Start a new game instead."
        )
    if state.get("game_over"):
        raise ApiError(
            f"this game already ended (status: {state['status']}). Start a new game."
        )
    if args.name:
        call(args.url, "POST", "/api/game/name", {"color": args.side, "name": args.name})
    print(f"joined as {args.side} ({kind}); opponent is {other}")
    if args.name:
        print(f"name set to {args.name!r}")
    if kind == "api-trainee":
        print("TRAINEE: phone-a-friend before every move, or forfeit. "
              "See references/trainee.md.")
    if kind == "centaur":
        print("CENTAUR: use 'suggest', not 'move' — a person at the board "
              "viewer finalizes every move. See references/centaur.md.")
    print(status_line(state, args.side))
    last = describe_move(state.get("last_move"))
    if last:
        print("last: " + last)
    return 0


def cmd_set(args):
    """Change a setting on the running game. Covers the display name, the
    difficulty, and the engine, which are otherwise three endpoints."""
    changed = []
    if args.name is not None:
        if not args.side:
            raise ApiError("--name needs --side, to say which color the name belongs to")
        call(args.url, "POST", "/api/game/name", {"color": args.side, "name": args.name})
        changed.append(f"{args.side} name = {args.name!r}")
    if args.level is not None:
        body = {"level": args.level}
        if args.side:
            body["color"] = args.side
        result = call(args.url, "POST", "/api/game/level", body)
        changed.append(f"levels = {result['levels']}")
    if args.engine is not None:
        body = {"engine": args.engine}
        if args.side:
            body["color"] = args.side
        result = call(args.url, "POST", "/api/game/engine", body)
        changed.append(f"engines = {result['engines']}")
    if not changed:
        raise ApiError("give at least one of --name, --level, or --engine")
    for line in changed:
        print("set " + line)
    return 0


def cmd_transcript(args):
    path = f"/api/game/transcript?include={args.include}"
    pgn = call(args.url, "GET", path, raw=True)
    if args.out:
        with open(args.out, "w") as handle:
            handle.write(pgn)
        # Print the path, not the file. An annotated transcript of a long
        # game is large, and the caller rarely needs it in context.
        print(f"wrote {len(pgn)} bytes to {args.out}")
    else:
        print(pgn)
    return 0


# ---- command line ---------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="chess.py", description="Play chess through the computer-chess REST API."
    )
    parser.add_argument("--url", default=os.environ.get("CHESS_API", DEFAULT_URL),
                        help=f"API base URL (default: {DEFAULT_URL}, or $CHESS_API)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    new = subparsers.add_parser("new", help="start a new game")
    new.add_argument("--white", required=True,
                     choices=["api-user", "api-trainee", "web-user", "engine", "centaur"])
    new.add_argument("--black", required=True,
                     choices=["api-user", "api-trainee", "web-user", "engine", "centaur"])
    new.add_argument("--level", type=int)
    new.add_argument("--white-level", type=int, dest="white_level")
    new.add_argument("--black-level", type=int, dest="black_level")
    new.add_argument("--engine", choices=["gnuchess", "stockfish"])
    new.add_argument("--white-engine", dest="white_engine",
                     choices=["gnuchess", "stockfish"])
    new.add_argument("--black-engine", dest="black_engine",
                     choices=["gnuchess", "stockfish"])
    new.add_argument("--white-name", dest="white_name")
    new.add_argument("--black-name", dest="black_name")
    new.add_argument("--friend-l10", type=int, dest="friend_level10_limit")
    new.add_argument("--friend-l20", type=int, dest="friend_level20_limit")
    new.add_argument("--friend-eval", type=int, dest="friend_eval_limit")
    new.set_defaults(func=cmd_new)

    turn = subparsers.add_parser(
        "turn", help="board, status and legal moves in one call (read-only)")
    turn.add_argument("--side", choices=["white", "black"],
                      help="your color; adds a whose-turn-it-is line and your budget")
    turn.add_argument("--brief", action="store_true",
                      help="omit the board, the FEN, and the tactics summary")
    turn.add_argument("--legal", action="store_true",
                      help="always list every legal move, however many there are")
    turn.set_defaults(func=cmd_turn)

    join = subparsers.add_parser("join", help="take over a side of a running game")
    join.add_argument("--side", required=True, choices=["white", "black"],
                      help="the color you will play")
    join.add_argument("--name", help="set this side's display name at the same time")
    join.set_defaults(func=cmd_join)

    setter = subparsers.add_parser(
        "set", help="change the display name, difficulty, or engine of a running game")
    setter.add_argument("--side", choices=["white", "black"],
                        help="which color to change; required with --name")
    setter.add_argument("--name", help="display name, up to 40 characters")
    setter.add_argument("--level", type=int, help="difficulty 0-20; affects engine sides only")
    setter.add_argument("--engine", choices=["gnuchess", "stockfish"])
    setter.set_defaults(func=cmd_set)

    move = subparsers.add_parser("move", help="submit one move")
    move.add_argument("move", help="the move in UCI, e.g. e2e4 or e7e8q")
    move.add_argument("--side", required=True, choices=["white", "black"],
                      help="the color you are playing; refuses to act if it is not that side's turn")
    # The skill requires all three on every move, and an 'api-trainee'
    # side forfeits the game for omitting either reasoning field. Making
    # them required here turns that rule into something the command line
    # enforces rather than something the caller must remember.
    move.add_argument("--chat", required=True, help="banter only, visible to everyone")
    move.add_argument("--tactical", required=True, help="private: concrete calculation")
    move.add_argument("--strategic", required=True, help="private: the longer-term plan")
    move.set_defaults(func=cmd_move)

    suggest = subparsers.add_parser(
        "suggest", help="suggest a move for a 'centaur' side (does not play it)")
    suggest.add_argument("move", help="the move in UCI, e.g. e2e4 or e7e8q")
    suggest.add_argument("--side", required=True, choices=["white", "black"],
                         help="the color you are suggesting for; refuses to act if it is not that side's turn")
    suggest.add_argument("--chat", required=True, help="banter only, visible to everyone")
    suggest.add_argument("--tactical", required=True, help="private: concrete calculation")
    suggest.add_argument("--strategic", required=True, help="private: the longer-term plan")
    suggest.set_defaults(func=cmd_suggest)

    friend = subparsers.add_parser(
        "phone-a-friend", help="ask for one or more hints or a position evaluation")
    friend.add_argument("queries", nargs="+", metavar="QUERY",
                        help="'eval', '10', '20', or LEVEL:ENGINE such as '20:stockfish'")
    friend.add_argument("--side", required=True, choices=["white", "black"],
                        help="the color you are playing; refuses to act if it is not that side's turn")
    friend.set_defaults(func=cmd_phone_a_friend)

    wait = subparsers.add_parser("wait", help="block until it is your turn")
    wait.add_argument("--side", required=True, choices=["white", "black"])
    wait.add_argument("--timeout", type=int, default=25)
    wait.set_defaults(func=cmd_wait)

    resign = subparsers.add_parser(
        "resign", help="end the game, by resignation or by abort")
    resign.add_argument("--side", choices=["white", "black"],
                        help="resign for this color. The other color wins.")
    resign.add_argument("--abort", action="store_true",
                        help="end the game with no winner instead of resigning")
    resign.set_defaults(func=cmd_resign)

    transcript = subparsers.add_parser(
        "transcript", help="fetch the PGN of a finished game")
    transcript.add_argument("--out", help="write to this file and print only the path")
    transcript.add_argument("--include", default="all", choices=["all", "moves"])
    transcript.set_defaults(func=cmd_transcript)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # The reader closed the pipe early — `... | head` is the usual
        # cause. Point stdout at the null device so the interpreter does
        # not try to flush it again at shutdown and print a traceback
        # over whatever the caller was actually reading.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
