import requests
import json
import random
import re
import time
import os
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BGA_EMAIL = os.environ["BGA_EMAIL"]
BGA_PASSWORD = os.environ["BGA_PASSWORD"]
BGA_PLAYER_ID = os.environ["BGA_PLAYER_ID"]

STORAGE_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(STORAGE_DIR, exist_ok=True)

SESSION_FILE = os.path.join(BASE_DIR, "storage/bga_session.json")
PAST_SUGGESTIONS_FILE = os.path.join(BASE_DIR, "storage/past_suggestions.json")
HISTORY_FILE = os.path.join(BASE_DIR, "bga_history.json")
GAMES_FILE = os.path.join(BASE_DIR, "bga_games.json")

BGA_TIMEOUT=2

def _create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
        "Accept": "*/*",
        "Accept-Language": "en-GB,en;q=0.9",
    })
    return session


def _load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r") as f:
            data = json.load(f)
        saved_time = datetime.fromisoformat(data["datetime"])
        age_hours = (datetime.now(timezone.utc) - saved_time).total_seconds() / 3600
        if age_hours >= 24:
            print("Saved session is older than 24 hours, logging in fresh.")
            return None
        print(f"Reusing saved session ({age_hours:.1f} hours old).")
        return data
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"Could not load saved session: {e}")
        return None


def _save_session(cookie_jar):
    cookies_list = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in cookie_jar
    ]
    data = {
        "cookies": cookies_list,
        "datetime": datetime.now(timezone.utc).isoformat(),
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Session saved to {SESSION_FILE}")


def _extract_request_token(resp):
    match = re.search(r"""requestToken['"]*\s*:\s*['"]([^'"]+)['"]""", resp.text)
    if match:
        return match.group(1)
    return None


def _login(email, password):
    session = _create_session()
    saved = _load_session()
    if saved:
        for c in saved["cookies"]:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
    else:
        print("Fetching login page for CSRF token...")
        resp = session.get("https://en.boardgamearena.com/account")
        time.sleep(BGA_TIMEOUT)
        login_request_token = _extract_request_token(resp)

        print("Checking username...")
        session.post(
            "https://en.boardgamearena.com/account/register/checkUserNameIsInUse.html",
            headers={
                "X-Request-Token": login_request_token,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://en.boardgamearena.com/account",
            },
            data={"username": email},
        )
        time.sleep(BGA_TIMEOUT)

        print("Logging in...")
        login_resp = session.post(
            "https://en.boardgamearena.com/account/auth/loginUserWithPassword.html",
            headers={
                "X-Request-Token": login_request_token,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://en.boardgamearena.com/account?step=2&page=login",
            },
            data={
                "username": email,
                "password": password,
                "remember_me": "false",
                "request_token": login_request_token,
            },
        )
        time.sleep(BGA_TIMEOUT)
        login_data = login_resp.json()
        if login_data.get("status") != 1:
            raise Exception(f"Login failed: {login_data}")
        login_result = login_data.get("data", {})
        if not login_result.get("success"):
            raise Exception(f"Login failed: {login_result.get('message', 'unknown error')}")
        print("Logged in successfully!")
        _save_session(session.cookies)

    print("Fetching fresh request token...")
    resp = session.get("https://en.boardgamearena.com/account")
    time.sleep(BGA_TIMEOUT)
    request_token = _extract_request_token(resp)
    return session, request_token


def _get_games(session, request_token, player_id, page=1, count=10):
    resp = session.get(
        "https://boardgamearena.com/gamestats/gamestats/getGames.html",
        headers={"X-Request-Token": request_token},
        params={
            "player": player_id,
            "opponent_id": 0,
            "finished": 1,
            "updateStats": 0,
            "page": page,
            "count": count,
            "dojo.preventCache": int(time.time() * 1000),
        },
    )
    time.sleep(BGA_TIMEOUT)
    return resp.json()


def pull_game_list():
    session = _create_session()
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    print("Fetching BGA game list page...")
    resp = session.get("https://en.boardgamearena.com/gamelist?section=all")
    time.sleep(BGA_TIMEOUT)
    resp.raise_for_status()

    # The game_list is embedded inside a globalUserInfos JS object in the HTML.
    match = re.search(r'globalUserInfos\s*=\s*(\{.*\})', resp.text)
    if not match:
        print("ERROR: Could not find globalUserInfos in page HTML.")
        with open(os.path.join(BASE_DIR, "debug_gamelist.html"), "w") as f:
            f.write(resp.text)
        raise SystemExit(1)

    raw_json = match.group(1)
    decoder = json.JSONDecoder()
    try:
        user_infos, _ = decoder.raw_decode(raw_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse globalUserInfos as JSON: {e}")
        raise SystemExit(1)

    if "game_list" not in user_infos:
        print(f"ERROR: globalUserInfos does not contain 'game_list' key.")
        print(f"Available keys: {list(user_infos.keys())}")
        raise SystemExit(1)

    game_list = user_infos["game_list"]

    # Resolve tag IDs to their names using game_tags
    tag_lookup = {t["id"]: t for t in user_infos.get("game_tags", [])}
    for game in game_list:
        resolved = []
        for tag_id, value in game.get("tags", []):
            tag_info = tag_lookup.get(tag_id)
            if tag_info:
                resolved.append({
                    "name": tag_info["name"],
                    "category": tag_info.get("cat", ""),
                    "value": value,
                })
            else:
                resolved.append({"id": tag_id, "value": value})
        game["tags"] = resolved
        player_numbers = game.get("player_numbers", [])
        game["min_player_number"] = min(player_numbers) if player_numbers else None
        game["max_player_number"] = max(player_numbers) if player_numbers else None

    with open(GAMES_FILE, "w") as f:
        json.dump(game_list, f, indent=2)

    print(f"Done! Extracted {len(game_list)} games to {GAMES_FILE}")


def pull_player_history():
    session, request_token = _login(BGA_EMAIL, BGA_PASSWORD)

    # Load existing history
    existing_tables = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            existing_tables = json.load(f)
        print(f"Loaded {len(existing_tables)} existing games from history.")

    existing_ids = {t["table_id"] for t in existing_tables}

    # Page through results, stopping when we hit games we already have
    new_tables = []
    page = 1
    found_duplicate = False
    while True:
        print(f"Fetching page {page}...")
        data = _get_games(session, request_token, BGA_PLAYER_ID, page=page)

        tables = data.get("data", {}).get("tables", [])
        if not tables:
            print("No more results.")
            break

        for table in tables:
            if table["table_id"] in existing_ids:
                print(f"  Found existing game {table['table_id']} — stopping.")
                found_duplicate = True
                break
            new_tables.append(table)

        if found_duplicate:
            break

        print(f"  Got {len(tables)} new games (total new: {len(new_tables)})")
        page += 1

    # Prepend new games (newest first) to existing history
    if new_tables:
        all_tables = new_tables + existing_tables
        with open(HISTORY_FILE, "w") as f:
            json.dump(all_tables, f, indent=2)
        print(f"\nDone! Added {len(new_tables)} new games. Total: {len(all_tables)}.")
    else:
        print("\nNo new games found. History is up to date.")


def _get_game_details(session, request_token, game_name):
    resp = session.post(
        "https://en.boardgamearena.com/gamelist/gamelist/gameDetails.html",
        headers={
            "X-Request-Token": request_token,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://en.boardgamearena.com/gamelist?section=all",
        },
        data=f"game={game_name}",
    )
    time.sleep(BGA_TIMEOUT)
    return resp.json().get("results", {})


def suggest_new_games(awards_only=False):
    with open(GAMES_FILE, "r") as f:
        games = json.load(f)

    played_ids = set()
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            for entry in json.load(f):
                played_ids.add(str(entry.get("game_id")))

    past_suggestions = []
    if os.path.exists(PAST_SUGGESTIONS_FILE):
        with open(PAST_SUGGESTIONS_FILE, "r") as f:
            past_suggestions = json.load(f)
    past_suggestion_ids = {str(s["id"]) for s in past_suggestions}

    AWARD_TAGS = {"Award-winning games", "BGA Awards '25 Nominee", "BGA Awards '25 Winner"}

    # Filter: must support 3 players, have weight >= 50, not already played, not previously suggested
    games = [g for g in games if (g.get("min_player_number") or 99) <= 3 and (g.get("max_player_number") or 0) >= 3 and (g.get("weight") or 0) >= 50 and str(g.get("id")) not in played_ids and str(g.get("id")) not in past_suggestion_ids]

    if awards_only:
        games = [g for g in games if AWARD_TAGS & {t.get("name") for t in g.get("tags") or []}]

    buckets = {"Short": [], "Medium": [], "Long": []}
    for g in games:
        dur = g.get("average_duration") or 0
        if dur <= 20:
            buckets["Short"].append(g)
        elif dur <= 45:
            buckets["Medium"].append(g)
        elif dur <= 75:
            buckets["Long"].append(g)
        else:
            pass

    session = _create_session()
    resp = session.get("https://en.boardgamearena.com/gamelist?section=all")
    time.sleep(BGA_TIMEOUT)
    request_token = _extract_request_token(resp)

    today = datetime.now().strftime("%Y-%m-%d")
    new_suggestions = []
    lines = ["New Game Suggestions:"]
    for label, pool in buckets.items():
        if not pool:
            lines.append(f"\n{label}: No games available")
            continue
        pick = random.choice(pool)
        new_suggestions.append({"id": str(pick["id"]), "name": pick["display_name_en"], "date": today})
        details = _get_game_details(session, request_token, pick["name"])
        description = ""
        for m in details.get("metadata", []):
            if m.get("type") == "description":
                description = " ".join(part.get("text", "") for part in m.get("value", []))
                # Truncate to 2 sentences or 150 characters, whichever is longer
                sentences = re.split(r'(?<=[.!?])\s+', description)
                two_sentences = " ".join(sentences[:2])
                if len(two_sentences) > len(description[:150]):
                    description = two_sentences
                else:
                    description = description[:150].rsplit(" ", 1)[0] + "..."
                break
        game_datas = [f"{pick.get('average_duration', '?')} min"]
        themes = [t["name"] for t in pick.get("tags", []) if t.get("category") == "Theme"]
        if themes:
            game_datas += themes
        if awards_only:
            awards = [t["name"] for t in pick.get("tags") or [] if t.get("name") in AWARD_TAGS]
            game_datas += awards

        lines.append(f"- {pick['display_name_en']} ({', '.join(game_datas)}) - {description}")

    output = "\n".join(lines)
    print(output)

    if new_suggestions:
        past_suggestions.extend(new_suggestions)
        with open(PAST_SUGGESTIONS_FILE, "w") as f:
            json.dump(past_suggestions, f, indent=2)

    return output


def suggest_forgotten_games():
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    # Build game_id -> display_name lookup from games file
    display_names = {}
    if os.path.exists(GAMES_FILE):
        with open(GAMES_FILE, "r") as f:
            for g in json.load(f):
                display_names[str(g["id"])] = g["display_name_en"]

    # Only consider plays with all three core players
    required_players = {"thomaspr", "alice2", "kristiah"}

    # Group plays by game_id, tracking play count and last played date
    game_stats = {}
    for entry in history:
        players = set(entry.get("player_names", "").split(","))
        if not required_players.issubset(players):
            continue
        gid = str(entry.get("game_id"))
        end_ts = int(entry.get("end", 0))
        if gid not in game_stats:
            game_stats[gid] = {"game_id": gid, "play_count": 0, "last_played": 0, "game_name": entry.get("game_name")}
        game_stats[gid]["play_count"] += 1
        if end_ts > game_stats[gid]["last_played"]:
            game_stats[gid]["last_played"] = end_ts

    # Filter: played 2+ times and last played more than 12 months ago
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=365)
    cutoff_ts = int(cutoff_date.timestamp())

    forgotten = [g for g in game_stats.values() if g["play_count"] >= 2 and g["last_played"] < cutoff_ts]

    if not forgotten:
        print("No forgotten games found.")
        return "No forgotten games found."

    # Sort by last_played ascending (oldest first)
    forgotten.sort(key=lambda g: g["last_played"])

    def _format_game(g):
        name = display_names.get(g["game_id"], g["game_name"])
        last = datetime.fromtimestamp(g["last_played"], tz=timezone.utc).strftime("%Y-%m-%d")
        return f"{name} — played {g['play_count']} times, last played {last}"

    lines = ["Forgotten Game Suggestions: "]

    # 3 random picks (excluding oldest to avoid duplicate, unless fewer than 4 games)
    picks = random.sample(forgotten, min(3, len(forgotten)))

    if picks:
        for pick in picks:
            lines.append(f"- {_format_game(pick)}")

    output = "\n".join(lines)
    print(output)
    return output


def send_signal_message(message):
    api_url = os.environ["SIGNAL_API_URL"]
    sender = os.environ["SIGNAL_SENDER"]
    recipient = os.environ["SIGNAL_RECIPIENT"]
    resp = requests.post(
        f"{api_url}/v2/send",
        json={
            "message": message,
            "number": sender,
            "recipients": [recipient],
        },
    )
    resp.raise_for_status()
    print("Signal message sent.")


if __name__ == "__main__":
    pull_player_history()
    pull_game_list()
