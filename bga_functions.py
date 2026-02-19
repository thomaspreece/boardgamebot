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
STATS_FILE = os.path.join(BASE_DIR, "bga_stats.json")

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

    generate_stats()


def generate_stats():
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    display_names = {}
    if os.path.exists(GAMES_FILE):
        with open(GAMES_FILE, "r") as f:
            for g in json.load(f):
                display_names[str(g["id"])] = g["display_name_en"]

    def _fmt_ts(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%-d %b %Y") if ts else None

    TRACKED_PLAYERS = {"kristiah", "thepengineer", "thomaspr", "alice2"}

    # Pre-compute first-play wins: iterate oldest-first to find each player's debut per game
    first_play_wins = {}   # player -> list of display names of games won on first play
    first_play_seen = set()  # (player, game_name) pairs already recorded
    for entry in reversed(history):
        entry_players = [p.strip() for p in entry.get("player_names", "").split(",") if p.strip()]
        entry_ranks = entry.get("ranks", "").split(",")
        entry_game = entry.get("game_name", "")
        entry_display = display_names.get(str(entry.get("game_id", "")), entry_game)
        for i, player in enumerate(entry_players):
            key = (player, entry_game)
            if key not in first_play_seen:
                first_play_seen.add(key)
                try:
                    rank = int(entry_ranks[i])
                except (IndexError, ValueError):
                    rank = None
                if rank == 1:
                    first_play_wins.setdefault(player, []).append(entry_display)

    player_stats = {}  # player_name -> aggregated stats
    game_stats = {}    # game_name -> aggregated stats
    year_stats = {}    # year_str -> aggregated stats

    for entry in history:
        player_names = [p.strip() for p in entry.get("player_names", "").split(",") if p.strip()]
        scores_raw = entry.get("scores", "").split(",")
        ranks_raw = entry.get("ranks", "").split(",")
        game_name = entry.get("game_name", "")
        game_id = str(entry.get("game_id", ""))
        start_ts = int(entry.get("start") or 0)
        end_ts = int(entry.get("end") or 0)
        duration_minutes = round((end_ts - start_ts) / 60) if end_ts > start_ts else None
        year = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y") if end_ts else None

        display = display_names.get(game_id, game_name)

        # --- Per-game ---
        if game_name not in game_stats:
            game_stats[game_name] = {
                "game_id": game_id,
                "display_name": display,
                "play_count": 0,
                "first_played_ts": end_ts,
                "last_played_ts": end_ts,
                "total_duration_minutes": 0,
                "duration_count": 0,
                "per_player": {},
            }
        gs = game_stats[game_name]
        gs["play_count"] += 1
        if end_ts and end_ts < gs["first_played_ts"]:
            gs["first_played_ts"] = end_ts
        if end_ts and end_ts > gs["last_played_ts"]:
            gs["last_played_ts"] = end_ts
        if duration_minutes is not None:
            gs["total_duration_minutes"] += duration_minutes
            gs["duration_count"] += 1

        # --- Per-year ---
        if year:
            if year not in year_stats:
                year_stats[year] = {
                    "total_games": 0,
                    "per_player": {},
                    "per_game": {},
                }
            ys = year_stats[year]
            ys["total_games"] += 1
            if game_name not in ys["per_game"]:
                ys["per_game"][game_name] = {"display_name": display, "play_count": 0}
            ys["per_game"][game_name]["play_count"] += 1

        for i, player in enumerate(player_names):
            try:
                rank = int(ranks_raw[i])
            except (IndexError, ValueError):
                rank = None

            # --- Global per-player ---
            if player not in player_stats:
                player_stats[player] = {
                    "games_played": 0,
                    "wins": 0,
                    "rank_sum": 0,
                    "rank_count": 0,
                    "per_game": {},
                }
            ps = player_stats[player]
            ps["games_played"] += 1
            if rank == 1:
                ps["wins"] += 1
            if rank is not None:
                ps["rank_sum"] += rank
                ps["rank_count"] += 1

            if game_name not in ps["per_game"]:
                ps["per_game"][game_name] = {"display_name": display, "plays": 0, "wins": 0}
            ps["per_game"][game_name]["plays"] += 1
            if rank == 1:
                ps["per_game"][game_name]["wins"] += 1

            # --- Per-game per-player ---
            if player not in gs["per_player"]:
                gs["per_player"][player] = {"plays": 0, "wins": 0}
            gs["per_player"][player]["plays"] += 1
            if rank == 1:
                gs["per_player"][player]["wins"] += 1

            # --- Per-year per-player ---
            if year:
                if player not in ys["per_player"]:
                    ys["per_player"][player] = {
                        "games_played": 0,
                        "wins": 0,
                        "rank_sum": 0,
                        "rank_count": 0,
                    }
                yp = ys["per_player"][player]
                yp["games_played"] += 1
                if rank == 1:
                    yp["wins"] += 1
                if rank is not None:
                    yp["rank_sum"] += rank
                    yp["rank_count"] += 1

    # --- Build output: per_player ---
    out_players = {}
    for player, ps in player_stats.items():
        if player not in TRACKED_PLAYERS:
            continue
        per_game_out = {}
        for gname, pg in ps["per_game"].items():
            per_game_out[gname] = {
                "display_name": pg["display_name"],
                "plays": pg["plays"],
                "wins": pg["wins"],
                "win_rate": round(pg["wins"] / pg["plays"], 3),
            }
        most_played = max(per_game_out, key=lambda g: per_game_out[g]["plays"]) if per_game_out else None
        eligible = [g for g in per_game_out if per_game_out[g]["plays"] >= 3]
        best_win_rate = max(eligible, key=lambda g: per_game_out[g]["win_rate"]) if eligible else None
        best_weighted = max(per_game_out, key=lambda g: per_game_out[g]["wins"] / (per_game_out[g]["plays"] + 3)) if per_game_out else None
        out_players[player] = {
            "games_played": ps["games_played"],
            "wins": ps["wins"],
            "win_rate": round(ps["wins"] / ps["games_played"], 3) if ps["games_played"] else 0,
            "avg_rank": round(ps["rank_sum"] / ps["rank_count"], 2) if ps["rank_count"] else None,
            "most_played_game": most_played,
            "best_win_rate_game": best_win_rate,
            "best_weighted_win_rate_game": best_weighted,
            "first_play_wins": len(first_play_wins.get(player, [])),
            "first_play_win_games": sorted(first_play_wins.get(player, [])),
            "per_game": per_game_out,
        }

    # --- Build output: per_game ---
    out_games = {}
    for gname, gs in game_stats.items():
        avg_duration = round(gs["total_duration_minutes"] / gs["duration_count"]) if gs["duration_count"] else None
        per_player_out = {
            player: {
                "plays": pp["plays"],
                "wins": pp["wins"],
                "win_rate": round(pp["wins"] / pp["plays"], 3),
            }
            for player, pp in gs["per_player"].items()
            if player in TRACKED_PLAYERS
        }
        out_games[gs["display_name"]] = {
            "game_id": gs["game_id"],
            "play_count": gs["play_count"],
            "first_played": _fmt_ts(gs["first_played_ts"]),
            "last_played": _fmt_ts(gs["last_played_ts"]),
            "avg_duration_minutes": avg_duration,
            "per_player": per_player_out,
        }

    # --- Build output: per_year ---
    out_years = {}
    for year, ys in sorted(year_stats.items()):
        most_played_game = max(ys["per_game"], key=lambda g: ys["per_game"][g]["play_count"]) if ys["per_game"] else None
        per_player_out = {}
        for player, yp in ys["per_player"].items():
            if player not in TRACKED_PLAYERS:
                continue
            per_player_out[player] = {
                "games_played": yp["games_played"],
                "wins": yp["wins"],
                "win_rate": round(yp["wins"] / yp["games_played"], 3) if yp["games_played"] else 0,
                "avg_rank": round(yp["rank_sum"] / yp["rank_count"], 2) if yp["rank_count"] else None,
            }
        per_game_out = {
            gd["display_name"]: gd["play_count"]
            for gname, gd in sorted(ys["per_game"].items(), key=lambda x: -x[1]["play_count"])
        }
        out_years[year] = {
            "total_games": ys["total_games"],
            "most_played_game": ys["per_game"][most_played_game]["display_name"] if most_played_game else None,
            "per_player": per_player_out,
            "per_game": per_game_out,
        }

    all_end_ts = [int(e.get("end") or 0) for e in history if e.get("end")]
    stats = {
        "generated_at": datetime.now(timezone.utc).strftime("%-d %b %Y %H:%M UTC"),
        "total_games": len(history),
        "date_range": {
            "first": _fmt_ts(min(all_end_ts)) if all_end_ts else None,
            "last": _fmt_ts(max(all_end_ts)) if all_end_ts else None,
        },
        "per_player": out_players,
        "per_game": out_games,
        "per_year": out_years,
    }

    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Stats written to {STATS_FILE}")
    return stats


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

    # session = _create_session()
    # resp = session.get("https://en.boardgamearena.com/gamelist?section=all")
    # time.sleep(BGA_TIMEOUT)
    # request_token = _extract_request_token(resp)

    today = datetime.now().strftime("%Y-%m-%d")
    new_suggestions = []
    lines = ["\n*New Game Suggestions:*"]
    for label, pool in buckets.items():
        if not pool:
            lines.append(f"\n{label}: No games available")
            continue
        pick = random.choice(pool)
        new_suggestions.append({"id": str(pick["id"]), "name": pick["display_name_en"], "date": today})
        # details = _get_game_details(session, request_token, pick["name"])
        # description = ""
        # for m in details.get("metadata", []):
        #     if m.get("type") == "description":
        #         description = " ".join(part.get("text", "") for part in m.get("value", []))
        #         # Truncate to 2 sentences or 150 characters, whichever is longer
        #         sentences = re.split(r'(?<=[.!?])\s+', description)
        #         two_sentences = " ".join(sentences[:2])
        #         if len(two_sentences) > len(description[:150]):
        #             description = two_sentences
        #         else:
        #             description = description[:150].rsplit(" ", 1)[0] + "..."
        #         break
        game_datas = [f"{pick.get('average_duration', '?')} min"]
        themes = [t["name"] for t in pick.get("tags", []) if t.get("category") == "Theme"]
        if themes:
            game_datas += themes
        if awards_only:
            awards = [t["name"] for t in pick.get("tags") or [] if t.get("name") in AWARD_TAGS]
            game_datas += awards

        lines.append(f"- **{pick['display_name_en']}** ({', '.join(game_datas)})")

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
        return f"**{name}** — played {g['play_count']} times, last played {last}"

    lines = ["\n*Forgotten Game Suggestions:* "]

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
            "text_mode": "styled",
            "number": sender,
            "recipients": [recipient],
        },
    )
    resp.raise_for_status()
    print("Signal message sent.")


if __name__ == "__main__":
    pull_player_history()
    pull_game_list()
