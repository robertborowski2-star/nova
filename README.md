# Nova — Personal AI Agent

A self-hosted AI agent with persistent memory, CFA-grade portfolio research, scheduled reports, and a Telegram interface. Built on Claude Sonnet. Designed to run 24/7 on a Raspberry Pi.

No cloud dependency except the Anthropic API. One process, one SQLite file, one `.env`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      You (Telegram)                     │
│                           │                             │
│                           ▼                             │
│                    telegram_bot.py                       │
│              receives messages, sends responses          │
│                           │                             │
│                           ▼                             │
│                       agent.py                          │
│              agentic loop (Claude + tools)               │
│                     ┌─────┴─────┐                       │
│                     ▼           ▼                        │
│               memory.py    portfolio.py                  │
│              (SQLite DB)   (yfinance + CSV)              │
│                     ▲                                    │
│                     │                                    │
│               scheduler.py                              │
│       auto-sends weekly/monthly/quarterly reports        │
│                                                         │
│  main.py — entry point, starts bot + scheduler          │
└─────────────────────────────────────────────────────────┘
```

### How the files connect

| File | Role |
|------|------|
| `main.py` | Entry point. Starts the Telegram bot and scheduler. Also supports `--test` mode for CLI queries. |
| `agent.py` | The brain. Builds a system prompt with injected memories, calls Claude in an agentic loop with tools (web search + portfolio research), extracts facts from responses. |
| `memory.py` | Persistent memory layer. SQLite database storing conversation history, user facts, and past reports. |
| `portfolio.py` | Market data layer. Loads holdings from CSV, fetches live prices via yfinance, builds analysis prompts. |
| `telegram_bot.py` | Telegram interface. Receives messages, routes to agent, sends responses. Whitelist security via chat ID. |
| `scheduler.py` | Scheduled report engine. Uses APScheduler to auto-send weekly/monthly/quarterly reports to Telegram. |

### The agentic loop (`agent.py`)

1. Build system prompt with your stored memories injected
2. Call Claude with tools available (`web_search` + `portfolio_research`)
3. If Claude calls a tool → execute it → feed results back → loop
4. When Claude finishes → extract any new facts → save to SQLite → reply

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/robertborowski2-star/nova.git
cd nova
pip install -r requirements.txt --break-system-packages
```

Dependencies:
- `anthropic` — Claude API client
- `python-telegram-bot` — async Telegram bot framework
- `apscheduler` — lightweight in-process scheduler
- `yfinance` — free market data via Yahoo Finance
- `pandas` — data manipulation for holdings
- `python-dotenv` — loads `.env` file into environment

### 2. Get a Telegram bot token

1. Open Telegram and message **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. "Nova") and a username (e.g. `nova_agent_bot`)
4. BotFather will reply with your **bot token** — save this
5. Message **@userinfobot** on Telegram — it will reply with your **chat ID**

### 3. Configure environment

```bash
cp .env.example .env   # or create .env manually
nano .env
```

Required variables:

| Variable | Description | Where to get it |
|----------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Your Claude API key | [platform.anthropic.com](https://platform.anthropic.com) |
| `TELEGRAM_TOKEN` | Bot token from BotFather | See step 2 above |
| `TELEGRAM_CHAT_ID` | Your personal chat ID (integer) | Message @userinfobot on Telegram |

Example `.env`:
```
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxx
TELEGRAM_TOKEN=7000000000:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=123456789
```

### 4. Create your holdings file

```bash
mkdir -p ~/portfolio
nano ~/portfolio/holdings.csv
```

#### `holdings.csv` column reference

| Column | Type | Description | Valid values |
|--------|------|-------------|--------------|
| `ticker` | string | Stock/ETF ticker symbol | e.g. `EQB`, `XEG`, `AAPL`, `URA` |
| `exchange` | string | Exchange the security trades on | `TSX`, `TSXV`, `NYSE`, `NASDAQ`, `NYSEARCA`, `BATS`, `CSE` |
| `shares` | number | Number of shares held | e.g. `150`, `500.5` |
| `avg_cost` | number | Average cost per share (in the security's currency) | e.g. `85.00`, `32.40` |
| `account_type` | string | Account where the position is held | `RRSP`, `TFSA`, `HOLDCO`, `PERSONAL`, `MARGIN` |
| `currency` | string | Currency the security trades in | `CAD`, `USD` |
| `asset_class` | string | Type of security | `EQUITY`, `ETF`, `REIT` |

Example:
```csv
ticker,exchange,shares,avg_cost,account_type,currency,asset_class
EQB,TSX,150,85.00,HOLDCO,CAD,EQUITY
XEG,TSX,500,19.50,RRSP,CAD,ETF
URA,NYSEARCA,200,32.40,RRSP,USD,ETF
AAPL,NASDAQ,50,175.00,PERSONAL,USD,EQUITY
```

### 5. Test without Telegram

```bash
python main.py --test "how are my stocks"
```

### 6. Run

```bash
python main.py
```

---

## Telegram commands

| Command | What it does |
|---------|-------------|
| `/start` | Show Nova's status and memory stats |
| `/memory` | See everything Nova remembers about you |
| `/forget category key` | Delete a specific fact (e.g. `/forget portfolio risk_tolerance`) |
| `/help` | Show all available commands and trigger phrases |

## Trigger phrases

Send any of these as a normal message (not a command):

| Message | What happens |
|---------|-------------|
| `weekly brief` | Weekly portfolio pulse — snapshot, changes, red flags (~400 words) |
| `monthly review` | Monthly sector/macro review with positioning notes (~600 words) |
| `quarterly review` | Full allocation analysis, retirement trajectory, currency exposure (~900 words) |
| `research EQB` | Single-ticker deep dive — valuation, news, analyst consensus (~500 words) |
| `how are my stocks` | Daily snapshot — current prices and day % change only, flags anything moving more than 2% |
| `what's moving` | Daily snapshot (same as above) |
| `daily check` | Daily snapshot (same as above) |
| Anything else | Nova answers using web search + her memory of you |

---

## Scheduled reports

Nova sends reports automatically to your Telegram — no action needed:

| Report | Schedule | What it covers |
|--------|---------|----------------|
| **Weekly Pulse** | Every Monday at 8:00 AM | Snapshot, material changes, red flags |
| **Monthly Review** | 1st of every month at 8:00 AM | Sector themes, macro backdrop, positioning |
| **Quarterly Review** | 1st of Jan, Apr, Jul, Oct at 8:00 AM | Full allocation, retirement trajectory, currency exposure |

If the Pi is offline at the scheduled time, reports will run up to 1 hour late (misfire grace period).

---

## How memory works

Nova uses three memory layers stored in a single SQLite database at `~/nova/nova.db`:

| Layer | What's stored | Persists between sessions? |
|-------|--------------|---------------------------|
| **Facts** | Things you tell Nova about yourself — preferences, goals, risk tolerance | Yes — permanent until you `/forget` them |
| **Conversations** | Last 20 messages loaded for context each API call | Yes — full history saved, last 20 used |
| **Reports** | Past research briefs (weekly, monthly, quarterly) | Yes — used to compare week-over-week changes |

### Automatic fact extraction

Nova extracts facts from your conversations automatically. Just tell her things naturally:

> "I prefer short reports"
> "My risk tolerance is moderate-aggressive"
> "I'm planning to retire at 52"

She'll remember it and inject it into every future system prompt. Facts are categorized as `personal`, `portfolio`, or `preference`.

### Memory commands

- `/memory` — see all stored facts
- `/forget category key` — remove a specific fact (e.g. `/forget preference report_style`)

---

## Systemd setup (Raspberry Pi)

To run Nova as a service that starts on boot:

```bash
# Copy the service file
sudo cp nova.service /etc/systemd/system/

# Reload systemd, enable and start
sudo systemctl daemon-reload
sudo systemctl enable nova
sudo systemctl start nova

# Check status
sudo systemctl status nova

# View logs
journalctl -u nova -f
```

The included `nova.service` file expects:
- Python at `/usr/bin/python3`
- Nova code at `/home/pi/nova/`
- `.env` file at `/home/pi/nova/.env`
- Runs as user `pi`
- Auto-restarts on failure (10 second delay)

---

## Estimated API costs

Nova uses Claude Sonnet via the Anthropic API. Costs are minimal for personal use:

| Report type | Est. cost per run |
|-------------|-------------------|
| Weekly pulse | $0.05 – $0.15 |
| Monthly review | $0.10 – $0.25 |
| Quarterly review | $0.20 – $0.40 |
| On-demand query | $0.02 – $0.10 |
| **Monthly total (typical)** | **~$1 – $3** |

Web search tool calls are included in the Anthropic API cost. yfinance data is free.

---

## Disclaimer

Nova is a personal research tool. It is **not financial advice**. All portfolio analysis is for informational and educational purposes only. Nova is not a registered investment advisor. Always do your own due diligence and consult a qualified financial professional before making investment decisions. Past performance does not guarantee future results.

---

## License

MIT License

Copyright (c) 2026 Robert Borowski

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
