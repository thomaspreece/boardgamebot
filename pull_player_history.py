import requests
from bs4 import BeautifulSoup
import json
import time
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import sys

# --- Config ---
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
BGA_EMAIL = os.environ["BGA_EMAIL"]
BGA_PASSWORD = os.environ["BGA_PASSWORD"]
PLAYER_ID = os.environ["BGA_PLAYER_ID"]
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bga_session.json")
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bga_history.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
})


def load_session():
    """Load saved session data if it exists and is less than 24 hours old."""
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


def save_session(cookie_jar):
    """Save session cookies and current datetime to file."""
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


def extract_request_token(resp):
    request_token = None 
    import re
    match = re.search(r"""requestToken['"]*\s*:\s*['"]([^'"]+)['"]""", resp.text)
    if match:
        request_token = match.group(1)
    
    return request_token

saved = load_session()
if saved:
    for c in saved["cookies"]:
        session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
else:
    # Step 1: Get the CSRF token from the login page
    print("Fetching login page for CSRF token...")
    resp = session.get("https://en.boardgamearena.com/account")
    login_request_token = extract_request_token(resp)

    # Step 1b: Check username
    print("Checking username...")
    check_resp = session.post(
        "https://en.boardgamearena.com/account/register/checkUserNameIsInUse.html",
        headers={
            "X-Request-Token": login_request_token,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://en.boardgamearena.com/account",
        },
        data={
            "username": BGA_EMAIL,
        },
    )
    print(f"Check response: {check_resp.status_code} {check_resp.text[:500]}")
    print(f"Cookies after check: {dict(session.cookies)}")

    # Step 2: Log in
    print("Logging in...")
    login_resp = session.post(
        "https://en.boardgamearena.com/account/auth/loginUserWithPassword.html",
        headers={
            "X-Request-Token": login_request_token,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://en.boardgamearena.com/account?step=2&page=login",
        },
        data={
            "username": BGA_EMAIL,
            "password": BGA_PASSWORD,
            "remember_me": "false",
            "request_token": login_request_token,
        },
    )
    print(f"Login response: {login_resp.status_code} {login_resp.text[:500]}")
    login_data = login_resp.json()
    if login_data.get("status") != 1:
        raise Exception(f"Login failed: {login_data}")
    login_result = login_data.get("data", {})
    if not login_result.get("success"):
        raise Exception(f"Login failed: {login_result.get('message', 'unknown error')}")
    print("Logged in successfully!")

    save_session(session.cookies)

# Always fetch a fresh request token
print("Fetching fresh request token...")
resp = session.get("https://en.boardgamearena.com/account")
request_token = extract_request_token(resp)

# Load existing history
existing_tables = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        existing_tables = json.load(f)
    print(f"Loaded {len(existing_tables)} existing games from history.")

existing_ids = {t["table_id"] for t in existing_tables}

# Fetch game history in pages
def get_games(player_id, page=1, count=10):
    resp = session.get(
        "https://boardgamearena.com/gamestats/gamestats/getGames.html",
        headers={
            "X-Request-Token": request_token,
        },
        params={
            "player": player_id,
            "opponent_id": 0,
            "finished": 1,      # 1 = finished games only
            "updateStats": 0,
            "page": page,
            "count": count,     # results per page
            "dojo.preventCache": int(time.time() * 1000),  # millisecond timestamp
        }
    )
    return resp.json()

# Page through results, stopping when we hit games we already have
new_tables = []
page = 1
found_duplicate = False
while True:
    print(f"Fetching page {page}...")
    data = get_games(PLAYER_ID, page=page)

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

    # Be polite — don't hammer the server
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