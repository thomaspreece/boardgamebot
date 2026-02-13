import requests
import json
import re
import os

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bga_games.json")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:147.0) Gecko/20100101 Firefox/147.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
})

print("Fetching BGA game list page...")
resp = session.get("https://en.boardgamearena.com/gamelist?section=all")
resp.raise_for_status()

# The game_list variable is embedded as inline JS in the HTML.
# It looks like: var game_list = [{...}, {...}, ...];
# or: game_list = [{...}, {...}, ...];
match = re.search(r'(?:var\s+)?game_list\s*=\s*(\[.*?\])\s*;', resp.text, re.DOTALL)
if not match:
    print("ERROR: Could not find game_list in page HTML.")
    print("Saving raw HTML for debugging...")
    with open("debug_gamelist.html", "w") as f:
        f.write(resp.text)
    raise SystemExit(1)

raw_js = match.group(1)

# The game_list is JS, not strict JSON — keys aren't quoted.
# Use a regex to quote unquoted keys: word characters before a colon.
def js_to_json(js_str):
    # Quote unquoted object keys (word chars followed by :)
    result = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', js_str)
    # Replace single-quoted strings with double-quoted
    # This is a simplified approach — handles most cases
    result = result.replace("'", '"')
    # Handle JS literals
    result = result.replace('true', 'true').replace('false', 'false').replace('null', 'null')
    return result

json_str = js_to_json(raw_js)

try:
    game_list = json.loads(json_str)
except json.JSONDecodeError as e:
    print(f"ERROR: Failed to parse game_list as JSON: {e}")
    # Save for debugging
    with open("debug_gamelist_json.txt", "w") as f:
        f.write(json_str)
    print("Saved attempted JSON to debug_gamelist_json.txt")
    raise SystemExit(1)

with open(OUTPUT_FILE, "w") as f:
    json.dump(game_list, f, indent=2)

print(f"Done! Extracted {len(game_list)} games to {OUTPUT_FILE}")
