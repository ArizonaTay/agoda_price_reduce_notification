# Agoda Price Drop Monitor

Monitors hotel room prices on Agoda and sends Telegram notifications when prices drop. Runs on **GitHub Actions** (free) once daily at 14:00 (UTC+8) — no server needed.

## Quick Start (GitHub Actions)

### 1. Fork this repo

Click the **Fork** button on GitHub.

### 2. Configure your hotels

Edit `prices.json` in your fork with the Agoda URL, room name, and dates you want to track:

```json
[
  {
    "name": "Hotel Name",
    "room_name": "Deluxe Room",
    "url": "https://www.agoda.com/en-sg/hotel-name/hotel/tokyo-jp.html?...",
    "last_price": null,
    "currency": "SGD"
  }
]
```

Set `last_price` to `null` or `0` for the first run — a baseline will be set automatically.

### 3. Get a Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts to create a new bot.
3. Copy the **token** (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

### 4. Get your Chat ID

1. Start a chat with your new bot and send any message.
2. Open a browser and visit:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
3. Look for `"chat":{"id":123456789}` — that number is your **Chat ID**.

> Alternatively, message [@userinfobot](https://t.me/userinfobot) on Telegram to get your Chat ID instantly.

### 5. Add repository secrets

In your fork on GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|--------|-------|
| `TELEGRAM_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID from step 4 |

### 6. Run the workflow

- Go to the **Actions** tab in your fork.
- Select **Monitor Agoda Hotel Prices** and click **Run workflow** → **Run workflow**.
- The workflow will run immediately and then once daily at 14:00 (UTC+8).

### 7. Get notified

- First run sets a **baseline** price.
- Subsequent runs send a **Price Drop** alert if a lower price is found.
- The workflow commits updated prices back to the repo.

## Local Development

### 1. Clone and install

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

Create a `.env` file in the project root:

```
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

If either is missing, the script prints Telegram messages to the console instead.

### 3. Run

```bash
python scraper.py
```

## How it works

- Uses **Playwright** (headless Chromium) to scrape Agoda hotel pages with anti-bot detection evasion (custom user agent, `navigator.webdriver` override, Singapore locale headers).
- Employs **three scraping strategies** in order:
  1. **`data-selenium` attribute selectors** — finds room name elements via Agoda's `data-selenium` attributes, walks up to the room card container, and extracts the lowest price.
  2. **CSS card selectors** — queries common room card CSS classes, locates the card containing the room name, and extracts the price near it.
  3. **Fallback broad scrape** — finds any price element on the page (filtering out discount/save labels) and picks the lowest.
- Compares against the stored lowest price in `prices.json`.
- Sends **HTML-formatted** Telegram alerts via the Bot API with hotel name, room type, price change, savings percentage, and a link.
- **Only updates** the stored price when a new lower price is found — price increases are logged but ignored.
- Saves debug screenshots and HTML when scraping fails for troubleshooting.
- If a login/captcha redirect is detected, raises an error immediately.
- The GitHub Actions workflow commits and pushes updated `prices.json` back to the repo automatically.
