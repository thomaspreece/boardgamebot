import argparse
import random
from bga_functions import pull_game_list, pull_player_history, suggest_forgotten_games, suggest_new_games, send_signal_message

SUGGEST_INTROS = [
    "It's time for this week's games roundup!",
    "Game night is calling — here's what's on the radar this week.",
    "Your weekly game suggestions have arrived!",
    "What should we play this week? Here are some ideas.",
    "Fresh off the press: your weekly game recommendations!",
    "Gather round — it's time to pick what we're playing this week.",
    "It's that time again — let's figure out what we're playing!",
    "Who needs Netflix? Here's what we should be playing this week.",
    "Another week, another chance to find your next favourite game.",
    "Here's what BGA has in store for us this week.",
    "Meeples at the ready! Here are this week's suggestions.",
]

def suggest_games(awards_only=False):
    parts = []
    result = suggest_forgotten_games()
    if result:
        parts.append(result.strip())
    result = suggest_new_games(awards_only)
    if result:
        parts.append(result.strip())
    return random.choice(SUGGEST_INTROS) + "\n\n" + "\n\n".join(parts)

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
