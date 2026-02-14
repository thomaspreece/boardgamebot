import requests
import json
import re
import time
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BGA_EMAIL = os.environ["BGA_EMAIL"]
BGA_PASSWORD = os.environ["BGA_PASSWORD"]
BGA_PLAYER_ID = os.environ["BGA_PLAYER_ID"]

SESSION_FILE = os.path.join(BASE_DIR, "bga_session.json")
HISTORY_FILE = os.path.join(BASE_DIR, "bga_history.json")
GAMES_FILE = os.path.join(BASE_DIR, "bga_games.json")


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
    return resp.json()


def pull_game_list():
    session = _create_session()
    session.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

    print("Fetching BGA game list page...")
    resp = session.get("https://en.boardgamearena.com/gamelist?section=all")
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
        time.sleep(2)
        page += 1

    # Prepend new games (newest first) to existing history
    if new_tables:
        all_tables = new_tables + existing_tables
        with open(HISTORY_FILE, "w") as f:
            json.dump(all_tables, f, indent=2)
        print(f"\nDone! Added {len(new_tables)} new games. Total: {len(all_tables)}.")
    else:
        print("\nNo new games found. History is up to date.")


if __name__ == "__main__":
    pull_player_history()
    pull_game_list()
