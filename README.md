# Tennis Odds Alert

This project scans tennis match odds from The Odds API to detect value bets (mismatches) and sends alerts to a Telegram channel. It uses a list of sharp bookmakers to calculate fair probabilities and compares them against target bookmakers to find edges.

## Features

- Fetches odds for ATP, WTA, and Challenger events.
- Calculates implied probabilities, fair probabilities, edge percentages, and Kelly stakes.
- Filters matches by minimum edge and time window.
- Sends formatted alerts to a Telegram channel.
- Avoids duplicate alerts via cooldown.

## Requirements

- Python 3.8+
- See `requirements.txt` for Python dependencies.

## Configuration

The behavior of the scanner is controlled via `config.yaml`:

- **odds_api.base_url** – Base URL for The Odds API.
- **odds_api.regions** – Regions of bookmakers to query (e.g., EU, US, UK).
- **odds_api.markets** – Markets to fetch (default `h2h` for match winner).
- **odds_api.sports_keys** – List of sport keys to scan.
- **books.sharp** – Sharp bookmakers used to determine fair probability.
- **books.targets** – Target bookmakers where value is sought.
- **filters.min_edge_pct** – Minimum percentage edge required to trigger an alert.
- **filters.min_decimal_odds** – Minimum decimal odds.
- **filters.max_start_time_hours** – Only scan events starting within this many hours.
- **alerting.cooldown_minutes** – Cooldown period to prevent duplicate alerts.

Edit this file to adjust your strategy.

## Usage

1. Clone the repository or download the code.
2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy the provided `.env.sample` to `.env` and fill in your secrets:

   - `ODDS_API_KEY` – API key from The Odds API.
   - `TELEGRAM_BOT_TOKEN` – Token for your Telegram bot.
   - `TELEGRAM_CHAT_ID` – Chat ID of the channel or user to receive alerts.

4. Run the scanner:

   ```bash
   python main.py
   ```

## Deployment

For deployment on platforms like Render or Railway, you can use the included Dockerfile or set up a background worker:

- **Docker**

  ```bash
  docker build -t tennis-odds-alert .
  docker run --rm --env-file .env tennis-odds-alert
  ```

- **Render Background Worker**

  1. Connect your repository.
  2. Set build command to `pip install -r requirements.txt`.
  3. Set start command to `python main.py`.
  4. Add your environment variables in Render’s dashboard.

## License

This project is provided for educational purposes without warranty.
