import argparse
from bga_functions import pull_game_list, pull_player_history, suggest_forgotten_games, suggest_new_games, send_signal_message

def suggest_games(awards_only=False):
    parts = []
    result = suggest_forgotten_games()
    if result:
        parts.append(result)
    result = suggest_new_games(awards_only)
    if result:
        parts.append(result)
    return "\n\n".join(parts)

COMMANDS = {
    "games": pull_game_list,
    "history": pull_player_history,
    "new": suggest_new_games,
    "forgotten": suggest_forgotten_games,
    "suggest": suggest_games
}

parser = argparse.ArgumentParser(description="BGA data tools")
parser.add_argument("command", choices=COMMANDS.keys(), help="Command to run")
parser.add_argument("--awards", action="store_true", help="Only suggest award-winning games")
parser.add_argument("--signal", action="store_true", help="Send suggestions via Signal")
args = parser.parse_args()

if args.command in ("new", "suggest"):
    result = COMMANDS[args.command](awards_only=args.awards)
elif args.command == "forgotten":
    result = COMMANDS[args.command]()
else:
    result = None
    COMMANDS[args.command]()

if args.signal and result:
    send_signal_message(result)
