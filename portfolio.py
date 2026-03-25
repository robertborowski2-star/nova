"""
portfolio.py — Market data fetching for Nova
=============================================
Loads holdings from ~/portfolio/holdings.csv and fetches live data via yfinance.
This is the same data layer from the OpenClaw skill, now integrated into Nova.
"""

import json
import pandas as pd
import yfinance as yf
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("nova.portfolio")

PORTFOLIO_DIR = Path.home() / "portfolio"
HOLDINGS_FILE = PORTFOLIO_DIR / "holdings.csv"

EXCHANGE_SUFFIX = {
    "TSX":     ".TO",
    "TSXV":    ".V",
    "NYSE":    "",
    "NASDAQ":  "",
    "NYSEARCA": "",
    "BATS":    "",
    "CSE": ".CN",
}


def load_holdings() -> pd.DataFrame:
    if not HOLDINGS_FILE.exists():
        raise FileNotFoundError(
            f"No holdings file at {HOLDINGS_FILE}\n"
            f"Create it with columns: ticker, exchange, shares, "
            f"avg_cost, account_type, currency, asset_class"
        )
    df = pd.read_csv(HOLDINGS_FILE)
    df.columns = df.columns.str.lower()
    df["ticker"]       = df["ticker"].str.upper().str.strip()
    df["exchange"]     = df["exchange"].str.upper().str.strip()
    df["account_type"] = df["account_type"].str.upper().str.strip()
    df["currency"]     = df["currency"].str.upper().str.strip()
    df["asset_class"]  = df["asset_class"].str.upper().str.strip()
    return df


def build_yf_ticker(ticker: str, exchange: str) -> str:
    return f"{ticker}{EXCHANGE_SUFFIX.get(exchange, '')}"


def fetch_market_data(df: pd.DataFrame) -> list[dict]:
    results = []
    for _, row in df.iterrows():
        yf_symbol = build_yf_ticker(row["ticker"], row["exchange"])
        try:
            info = yf.Ticker(yf_symbol).info
            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            avg_cost = float(row["avg_cost"])
            shares   = float(row["shares"])

            if current_price:
                market_value    = current_price * shares
                unrealised_pnl  = market_value - (avg_cost * shares)
                unrealised_pct  = ((current_price - avg_cost) / avg_cost) * 100
            else:
                market_value = unrealised_pnl = unrealised_pct = None

            results.append({
                "ticker":           row["ticker"],
                "exchange":         row["exchange"],
                "yf_symbol":        yf_symbol,
                "account_type":     row["account_type"],
                "currency":         row["currency"],
                "asset_class":      row["asset_class"],
                "shares":           shares,
                "avg_cost":         avg_cost,
                "current_price":    current_price,
                "day_change_pct":   info.get("regularMarketChangePercent"),
                "week_52_high":     info.get("fiftyTwoWeekHigh"),
                "week_52_low":      info.get("fiftyTwoWeekLow"),
                "ytd_return":       info.get("ytdReturn"),
                "pe_ratio":         info.get("trailingPE"),
                "forward_pe":       info.get("forwardPE"),
                "dividend_yield":   info.get("dividendYield"),
                "market_cap":       info.get("marketCap"),
                "analyst_target":   info.get("targetMeanPrice"),
                "analyst_rating":   info.get("recommendationKey"),
                "sector":           info.get("sector") or info.get("category"),
                "market_value":     market_value,
                "unrealised_pnl":   unrealised_pnl,
                "unrealised_pct":   unrealised_pct,
                "data_error":       None,
            })
        except Exception as e:
            results.append({
                "ticker": row["ticker"], "exchange": row["exchange"],
                "yf_symbol": yf_symbol, "data_error": str(e),
                **{k: None for k in [
                    "account_type", "currency", "asset_class", "shares",
                    "avg_cost", "current_price", "day_change_pct",
                    "week_52_high", "week_52_low", "ytd_return", "pe_ratio",
                    "forward_pe", "dividend_yield", "market_cap",
                    "analyst_target", "analyst_rating", "sector",
                    "market_value", "unrealised_pnl", "unrealised_pct"
                ]}
            })
    return results


def build_analysis_prompt(market_data: list[dict], mode: str = "weekly") -> str:
    """Return a prompt fragment with live data and mode instructions."""
    data_block = json.dumps(market_data, indent=2, default=str)

    mode_instructions = {
        "daily":     "DAILY SNAPSHOT — prices and day % change only. Flag anything moving more than 2% today with a brief one-line reason if available. No macro commentary, no news summary. Table format only, under 150 words.",
        "weekly":    "WEEKLY PULSE — tight snapshot, material changes, red flags. ~400 words.",
        "monthly":   "MONTHLY REVIEW — sector themes, macro backdrop, positioning. ~600 words.",
        "quarterly": "QUARTERLY REVIEW — full allocation analysis, retirement trajectory, "
                     "currency exposure, key themes for next quarter. ~900 words.",
    }

    if mode.startswith("single:"):
        ticker = mode.split(":")[1].upper()
        instruction = (
            f"SINGLE-STOCK DEEP DIVE on {ticker}: valuation vs peers, "
            f"recent news, analyst consensus, 52w range position, risks, watchpoints. ~500 words."
        )
    else:
        instruction = mode_instructions.get(mode, mode_instructions["weekly"])

    return f"""
LIVE PORTFOLIO DATA (fetched {datetime.now().strftime('%Y-%m-%d %H:%M')} via yfinance):
{data_block}

ANALYSIS MODE: {instruction}

Search the web for recent news on each holding and relevant macro themes 
before writing your analysis.
""".strip()
