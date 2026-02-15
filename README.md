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

## How it works

The tool scrapes data from BGA's web interface since there is no official public API.

- **Game list**: Fetches the BGA game list page and extracts the game catalogue from the embedded `globalUserInfos` JavaScript object. This includes game metadata like player counts, duration, weight, and tags. No login required.
- **Play history**: Logs in with your BGA credentials (email/password) to access the `getGames.html` endpoint, which returns your finished games paginated. It incrementally fetches new games by stopping when it encounters a game already in the local history.
- **Game details**: Fetches individual game descriptions from the `gameDetails.html` endpoint. No login required, but a request token is extracted from the game list page.
- **Session management**: Login sessions are cached in `storage/bga_session.json` and reused for up to 24 hours to avoid unnecessary logins.
- **Rate limiting**: A 2-second delay (`BGA_TIMEOUT`) is applied after every request to BGA.

## Usage

```bash
python cli.py <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `games` | Pull the full game list from BGA and save to `bga_games.json` |
| `history` | Pull your play history and save to `bga_history.json` |
| `new` | Suggest unplayed games for each duration category (Short, Medium, Long) |
| `forgotten` | Suggest games you've played 2+ times but not in the last 12 months |
| `suggest` | Run both `forgotten` and `new` together |

### Options

| Option | Applies to | Description |
|--------|-----------|-------------|
| `--awards` | `new`, `suggest` | Only suggest award-winning or BGA Awards nominated/winning games |

### Examples

```bash
# Pull latest game list
python cli.py games

# Update play history
python cli.py history

# Get new game suggestions
python cli.py new

# Get new suggestions from award-winning games only
python cli.py new --awards

# Get forgotten game suggestions
python cli.py forgotten

# Get both forgotten and new suggestions
python cli.py suggest
python cli.py suggest --awards
```
