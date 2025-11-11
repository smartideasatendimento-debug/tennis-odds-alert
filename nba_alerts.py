"""
Script to send NBA scoring trend alerts via Telegram.

This script uses the BallDontLie NBA API to fetch each player's last
five games and checks two patterns:

1. Pattern A: In the last five games the player scored 20 or more
   points in four consecutive games followed by a game below 20 points.
2. Pattern B: The player scored 20 or more points in all of the last
   five games.

Players to monitor can be provided via the ``NBA_PLAYERS`` environment
variable as a comma‑separated list of names (e.g., "LeBron James, Nikola
Jokic"). If not provided, a small default list of notable players is
used. An API key for BallDontLie is expected via the
``BALLDONTLIE_API_KEY`` environment variable. See
https://www.balldontlie.io/ for details on obtaining a free API key.

Alerts are sent using the same Telegram bot and chat configured via
``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` environment variables.

Note: This script may make many requests if monitoring many players.
Please respect the API's rate limits.
"""

import os
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Tuple

import requests

BALLDONTLIE_API_BASE = "https://api.balldontlie.io/v1"

# Environment variables
BALLDONTLIE_API_KEY = os.getenv("BALLDONTLIE_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Default players to monitor if NBA_PLAYERS is not set.
DEFAULT_PLAYERS = [
    "Nikola Jokic",
    "Luka Doncic",
    "Stephen Curry",
    "Kevin Durant",
    "Jayson Tatum",
    "Giannis Antetokounmpo",
    "LeBron James",
]


def send_telegram(text: str) -> None:
    """Send a message to the configured Telegram chat."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("[WARN] Telegram not configured.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(f"[WARN] Failed to send Telegram message: {exc}")


def get_player_id(name: str) -> int:
    """Look up a player's ID using their full name. Returns 0 if not found."""
    # The players endpoint supports searching by name via the "search" query.
    params = {"search": name, "per_page": 1}
    try:
        headers = {}
        if BALLDONTLIE_API_KEY:
            headers["Authorization"] = BALLDONTLIE_API_KEY
        r = requests.get(
            f"{BALLDONTLIE_API_BASE}/players",
            params=params,
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        if data:
            return int(data[0]["id"])
    except Exception as exc:
        print(f"[WARN] Failed to fetch player ID for {name}: {exc}")
    return 0


def get_last_five_games_points(player_id: int) -> List[int]:
    """Fetch the point totals from the player's last five games.

    Returns a list of integers (points) ordered from oldest to most
    recent. If fewer than five games are available, returns an empty
    list.
    """
    # Use the stats endpoint filtered by player_id, limit results to 5.
    # We request a large per_page and then sort by game date, because
    # the API may not support ordering parameters on its own.
    params = {
        "player_ids[]": player_id,
        "per_page": 25,
    }
    try:
        headers = {}
        if BALLDONTLIE_API_KEY:
            headers["Authorization"] = BALLDONTLIE_API_KEY
        r = requests.get(
            f"{BALLDONTLIE_API_BASE}/stats",
            params=params,
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        # Sort by game date in descending order (most recent first)
        stats_sorted = sorted(
            data, key=lambda s: s.get("game", {}).get("date", ""), reverse=True
        )
        # Extract the first five games
        last_five = stats_sorted[:5]
        # We want the oldest first, so reverse the slice
        last_five.reverse()
        points = [int(g.get("pts", 0)) for g in last_five]
        return points if len(points) == 5 else []
    except Exception as exc:
        print(f"[WARN] Failed to fetch last games for player ID {player_id}: {exc}")
    return []


def qualifies_pattern_a(points: List[int]) -> bool:
    """Return True if points match the pattern [>=20, >=20, >=20, >=20, <20]."""
    return (
        len(points) == 5
        and all(p >= 20 for p in points[:4])
        and points[4] < 20
    )


def qualifies_pattern_b(points: List[int]) -> bool:
    """Return True if points match the pattern [>=20, >=20, >=20, >=20, >=20]."""
    return len(points) == 5 and all(p >= 20 for p in points)


def sanitize_md(text: str) -> str:
    """Escape characters that have special meaning in Telegram MarkdownV2."""
    specials = "_[]()~`>#+-=|{}.!"
    for ch in specials:
        text = text.replace(ch, f"\{ch}")
    return text


def format_alert(player_name: str, points: List[int], pattern: str) -> str:
    """Format the alert message for a player."""
    parts = [
        "🏀 Tendência de pontuação na NBA",
        f"Jogador: {player_name}",
        f"Últimas 5 partidas: {', '.join(str(p) for p in points)} pontos",
        f"Padrão: {pattern}",
    ]
    return "\n".join(sanitize_md(p) for p in parts)


def main() -> None:
    # Determine the list of players to monitor
    players_env = os.getenv("NBA_PLAYERS", "").strip()
    if players_env:
        player_names = [p.strip() for p in players_env.split(",") if p.strip()]
    else:
        player_names = DEFAULT_PLAYERS

    # Cache to avoid duplicate alerts within a day (timestamp in seconds)
    cache_file = "nba_alerts_cache.json"
    try:
        with open(cache_file, "r") as f:
            sent_cache: Dict[str, float] = json.load(f)
    except Exception:
        sent_cache = {}

    now_ts = time.time()
    # We'll avoid sending duplicate alerts for the same player within 12 hours
    cooldown_seconds = 12 * 3600

    for name in player_names:
        player_id = get_player_id(name)
        if not player_id:
            continue
        points = get_last_five_games_points(player_id)
        if not points:
            continue
        key = f"{player_id}|{points}"  # Unique key for this state
        last_sent = sent_cache.get(key, 0)
        if now_ts - last_sent < cooldown_seconds:
            continue
        pattern = None
        if qualifies_pattern_b(points):
            pattern = "5 jogos consecutivos com 20+ pontos"
        elif qualifies_pattern_a(points):
            pattern = "4 jogos com 20+ seguidos de um <20"
        if pattern:
            msg = format_alert(name, points, pattern)
            send_telegram(msg)
            sent_cache[key] = now_ts

    # Save cache
    try:
        with open(cache_file, "w") as f:
            json.dump(sent_cache, f)
    except Exception:
        pass

    print("NBA scan finished")


if __name__ == "__main__":
    main()
