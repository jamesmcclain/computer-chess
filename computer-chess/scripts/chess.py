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
    chess.py turn --side white
    chess.py phone-a-friend --side white eval 20:stockfish
    chess.py move --side white e2e4 --chat "..." --tactical "..." --strategic "..."
    chess.py wait --side white
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
    """One line of remaining phone-a-friend budget for `side`. Values
    are the counts left, `-1` for unlimited; see the skill's
    references/phone-a-friend.md."""
    budget = (state.get("phone_a_friend") or {}).get(side)
    if not budget:
        return None
    parts = [f"{name} {value}" for name, value in sorted(budget.items())]
    return "budget: " + "  ".join(parts)


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
    line = friend_line(state, args.side) if args.side else None
    if line:
        print(line)
    moves = call(args.url, "GET", "/api/game/legal-moves")
    print(f"legal ({moves['count']}): {moves['moves']}")
    return 0


def cmd_move(args):
    verify_side(args.url, args.side)
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
    state = call(args.url, "POST", "/api/game/resign", {"player": args.side})["state"]
    print(status_line(state))
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
                     choices=["api-user", "api-trainee", "web-user", "engine"])
    new.add_argument("--black", required=True,
                     choices=["api-user", "api-trainee", "web-user", "engine"])
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
    turn.add_argument("--brief", action="store_true", help="omit the board and FEN")
    turn.set_defaults(func=cmd_turn)

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

    resign = subparsers.add_parser("resign", help="resign for one side")
    resign.add_argument("--side", required=True, choices=["white", "black"])
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
