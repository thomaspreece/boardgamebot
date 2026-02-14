import argparse
from bga_functions import pull_game_list, pull_player_history

COMMANDS = {
    "games": pull_game_list,
    "history": pull_player_history,
}

parser = argparse.ArgumentParser(description="BGA data tools")
parser.add_argument("command", choices=COMMANDS.keys(), help="Command to run")
args = parser.parse_args()

COMMANDS[args.command]()
