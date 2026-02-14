import argparse
from bga_functions import pull_game_list, pull_player_history, suggest_games

COMMANDS = {
    "games": pull_game_list,
    "history": pull_player_history,
    "suggest": suggest_games,
}

parser = argparse.ArgumentParser(description="BGA data tools")
parser.add_argument("command", choices=COMMANDS.keys(), help="Command to run")
parser.add_argument("--awards", action="store_true", help="Only suggest award-winning games")
args = parser.parse_args()

if args.command == "suggest":
    COMMANDS[args.command](awards_only=args.awards)
else:
    COMMANDS[args.command]()
