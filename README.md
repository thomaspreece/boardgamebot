# BGA Agent

CLI tool for pulling data from [Board Game Arena](https://boardgamearena.com) and getting game suggestions.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file with your BGA credentials:

```
BGA_EMAIL=your_email
BGA_PASSWORD=your_password
BGA_PLAYER_ID=your_player_id
```

## Usage

```bash
python cli.py <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `games` | Pull the full game list from BGA and save to `bga_games.json` |
| `history` | Pull your play history and save to `bga_history.json` |
| `suggest` | Suggest an unplayed game for each duration category (Short, Medium, Long) |

### Options

| Option | Applies to | Description |
|--------|-----------|-------------|
| `--awards` | `suggest` | Only suggest award-winning or BGA Awards nominated/winning games |

### Examples

```bash
# Pull latest game list
python cli.py games

# Update play history
python cli.py history

# Get game suggestions
python cli.py suggest

# Get suggestions from award-winning games only
python cli.py suggest --awards
```
