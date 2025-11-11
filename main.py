import os, json, requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Tuple
from tenacity import retry, stop_after_attempt, wait_exponential

# -------------------- CONFIG BÁSICA --------------------
SPORT_KEYS = [
    "tennis_atp",
    "tennis_wta",
    "tennis_atp_challenger",
    "tennis_wta_challenger",
]
REGIONS = ["eu", "uk", "us"]  # regiões de books na The Odds API
MARKETS = ["h2h"]  # mercado head‑to‑head
SHARP_BOOKS = ["pinnacle", "betfair_exchange"]  # base para prob. justa
TARGET_BOOKS = {
    "bet365",
    "williamhill",
    "unibet",
    "betway",
    "bwin",
    "888sport",
    "betfair",
}  # onde buscamos valor

MIN_EDGE_PCT = 3.0  # edge mínima para alertar
MIN_DECIMAL_ODDS = 1.50  # odds mínimas
MAX_START_TIME_HOURS = 48  # só jogos até 48h à frente
COOLDOWN_MINUTES = 90  # evita alerta duplicado

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# -------------------- ENV REQUERIDOS --------------------
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

if not (ODDS_API_KEY and TG_TOKEN and TG_CHAT_ID):
    raise RuntimeError(
        "Defina ODDS_API_KEY, TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nas variáveis de ambiente."
    )

# -------------------- HELPERS --------------------
def implied_prob(decimal_odds: float) -> float:
    return 0.0 if not decimal_odds or decimal_odds <= 0 else 1.0 / decimal_odds


def kelly_fraction(decimal_odds: float, fair_prob: float) -> float:
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - fair_prob
    edge = b * fair_prob - q
    f = edge / b
    return max(0.0, f)


def sanitize_md(text: str) -> str:
    specials = "_[]()~`>#+-=|{}.!"
    for ch in specials:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_alert(payload: Dict[str, Any]) -> str:
    title = "🎾 Alerta de valor em tênis"
    matchup = f"{payload['away']} vs {payload['home']}"
    when = payload["start_time_local"]
    line = f"Mercado h2h - {payload['pick_name']}"
    price = f"{payload['book']} {payload['price']:.2f} - edge {payload['edge_pct']:.1f}%"
    fair = f"Prob justa {payload['fair_prob']*100:.1f}% - Kelly {payload['kelly']*100:.1f}%"
    comp = f"Base justa: {payload['basis']}"
    msg = "\n".join(
        sanitize_md(s) for s in [title, matchup, when, line, price, fair, comp] if s
    )
    return msg


def event_key(
    ev_id: str, book: str, pick_name: str, price: float, fair_prob: float, basis: str
) -> str:
    return f"{ev_id}|{book}|{pick_name}|{price:.3f}|{fair_prob:.5f}|{basis}"


def now_utc():
    return datetime.now(timezone.utc)


# -------------------- API CLIENTS --------------------
class OddsAPI:
    def __init__(self, api_key: str, base_url: str = ODDS_API_BASE):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
    def odds(
        self, sport_key: str, regions: List[str], markets: List[str]
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/sports/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": ",".join(regions),
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=25)
        resp.raise_for_status()
        return resp.json()


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()


# -------------------- CORE --------------------
def pick_fair_prob(book_lines: Dict[str, float]) -> Tuple[str, float]:
    # 1) Tenta sharp; 2) média das implied
    for sb in SHARP_BOOKS:
        if sb in book_lines and book_lines[sb] and book_lines[sb] > 1.0:
            return sb, 1.0 / book_lines[sb]
    imps = [1.0 / x for x in book_lines.values() if x and x > 1.0]
    if not imps:
        return "", 0.0
    return "consensus", sum(imps) / len(imps)


def run_scan():
    api = OddsAPI(ODDS_API_KEY)
    cache_file = "sent_cache.json"
    try:
        with open(cache_file, "r") as f:
            sent = json.load(f)
    except Exception:
        sent = {}

    now = now_utc()
    sent_any = False

    for sport_key in SPORT_KEYS:
        try:
            events = api.odds(sport_key, REGIONS, MARKETS)
        except Exception as e:
            print(f"[WARN] Falha ao puxar {sport_key}: {e}")
            continue

        for ev in events:
            try:
                commence = datetime.fromisoformat(
                    ev["commence_time"].replace("Z", "+00:00")
                )
            except Exception:
                continue
            if commence - now > timedelta(hours=MAX_START_TIME_HOURS):
                continue

            # Monta {book -> {jogador: preço}}
            lines: Dict[str, Dict[str, float]] = {}
            for b in ev.get("bookmakers", []):
                bk = b.get("key", "")
                for m in b.get("markets", []):
                    if m.get("key") != "h2h":
                        continue
                    out = m.get("outcomes", [])
                    if len(out) < 2:
                        continue
                    prices = {o["name"]: o.get("price") for o in out if "name" in o}
                    if prices:
                        lines[bk] = prices

            if not lines:
                continue

            # Descobre os dois participantes
            participants = set()
            for d in lines.values():
                participants.update(d.keys())
            if len(participants) != 2:
                continue
            p1, p2 = list(participants)

            # Preços por participante
            p1_prices = {bk: d.get(p1) for bk, d in lines.items() if d.get(p1)}
            p2_prices = {bk: d.get(p2) for bk, d in lines.items() if d.get(p2)}

            basis1, fair1 = pick_fair_prob(p1_prices)
            basis2, fair2 = pick_fair_prob(p2_prices)
            if fair1 <= 0 or fair2 <= 0:
                continue
            s = fair1 + fair2
            if s > 0:
                fair1, fair2 = fair1 / s, fair2 / s

            # Checa valor nas casas‑alvo
            for pick_name, fair_prob, book_prices, basis in [
                (p1, fair1, p1_prices, basis1),
                (p2, fair2, p2_prices, basis2),
            ]:
                if fair_prob <= 0:
                    continue
                for book, price in book_prices.items():
                    if book not in TARGET_BOOKS:
                        continue
                    if not price or price < MIN_DECIMAL_ODDS:
                        continue
                    edge = price * fair_prob - 1.0
                    if edge < (MIN_EDGE_PCT / 100.0):
                        continue

                    k = event_key(
                        ev["id"], book, pick_name, float(price), float(fair_prob), basis
                    )
                    last = sent.get(k, 0)
                    if (now.timestamp() - last) < (COOLDOWN_MINUTES * 60):
                        continue

                    payload = {
                        "away": ev.get("away_team", "Jogador A"),
                        "home": ev.get("home_team", "Jogador B"),
                        "start_time_local": commence.astimezone().strftime("%d/%m %H:%M"),
                        "pick_name": pick_name,
                        "book": book,
                        "price": float(price),
                        "edge_pct": 100.0 * edge,
                        "fair_prob": float(fair_prob),
                        "kelly": kelly_fraction(float(price), float(fair_prob)),
                        "basis": basis or "consensus",
                    }
                    try:
                        send_telegram(TG_TOKEN, TG_CHAT_ID, format_alert(payload))
                        sent[k] = now.timestamp()
                        sent_any = True
                        print(
                            f"[OK] Alerta enviado: {book} {pick_name} @ {price:.2f} (edge {100*edge:.1f}%)"
                        )
                    except Exception as e:
                        print(f"[WARN] Falha ao enviar Telegram: {e}")

    with open(cache_file, "w") as f:
        json.dump(sent, f)
    print(
        "Scan finished" + (" (com alertas)" if sent_any else " (sem alertas)")
    )


if __name__ == "__main__":
    run_scan()
