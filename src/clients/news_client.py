import logging
import os
from typing import Any, Dict

import requests

logger = logging.getLogger(__name__)

# Load credentials
ALPACA_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("ALPACA_KEY_ID")
ALPACA_SECRET = os.getenv("ALPACA_SECRET_KEY")
ALPACA_URL = os.getenv("ALPACA_API_URL") or "https://data.alpaca.markets"

# Translate trading URL domains to their market data equivalents
if "paper-api.alpaca.markets" in ALPACA_URL:
    DATA_HOST = ALPACA_URL.replace("paper-api.alpaca.markets", "data.alpaca.markets")
elif "api.alpaca.markets" in ALPACA_URL:
    DATA_HOST = ALPACA_URL.replace("api.alpaca.markets", "data.alpaca.markets")
else:
    DATA_HOST = ALPACA_URL

# Strip any path suffixes from the domain host
if "/v2" in DATA_HOST:
    DATA_HOST = DATA_HOST.split("/v2")[0]


def analyze_sentiment_deterministically(headline: str, summary: str) -> tuple[str, str]:
    """
    Perform a 100% deterministic lexicon-based sentiment analysis and catalyst tagging.
    """
    text = (headline + " " + summary).lower()

    # Positive and negative word lists
    pos_words = [
        "gain",
        "beat",
        "rally",
        "upgrade",
        "outperform",
        "bull",
        "growth",
        "high",
        "rise",
        "positive",
        "strong",
        "advance",
        "surge",
    ]
    neg_words = [
        "fall",
        "drop",
        "miss",
        "downgrade",
        "underperform",
        "bear",
        "decline",
        "low",
        "slump",
        "negative",
        "weak",
        "plunge",
        "slide",
    ]

    pos_count = sum(text.count(word) for word in pos_words)
    neg_count = sum(text.count(word) for word in neg_words)

    # Determine catalyst category
    catalyst = "General Market"
    if any(w in text for w in ["earnings", "revenue", "eps", "profit", "net income"]):
        catalyst = "Earnings"
    elif any(
        w in text for w in ["upgrade", "downgrade", "price target", "rating", "target raised"]
    ):
        catalyst = "Analyst Rating"
    elif any(w in text for w in ["dividend", "split", "merger", "acquisition", "buyback"]):
        catalyst = "Corporate Action"

    sentiment = "NEUTRAL"
    if pos_count > neg_count:
        sentiment = "BULLISH"
    elif neg_count > pos_count:
        sentiment = "BEARISH"

    return sentiment, catalyst


def analyze_sentiment_local_llm(headline: str, summary: str, days: int = 2) -> tuple[str, str]:
    """
    Classify news sentiment and catalyst using a local LLM via Ollama.
    Falls back to lexicon-based analysis if Ollama is unavailable or fails.
    """
    import json

    url = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/api/generate")
    model = os.getenv("LOCAL_LLM_MODEL", "llama3")

    prompt = f"""
You are a financial analyst. Read the following news headlines and summaries for a ticker over the last {days} days.

News Context:
{headline}

Extract the primary recent catalyst and its sentiment. You must output ONLY a raw JSON object matching exactly this schema:
{{
  "catalyst": "<summary of the main driving catalyst, e.g. 'Q3 beat + raised FY guidance'>",
  "catalyst_type": "<one of: earnings|upgrade|contract|product|litigation|none>",
  "sentiment": "<one of: bullish|bearish|neutral>",
  "corroborates_direction": <boolean true/false>,
  "event_flags": ["<array of specific keywords like 'guidance raise'>"],
  "as_of": "<current date>"
}}

CRITICAL INSTRUCTION: Keep your internal `<think>` block extremely concise (under 30 words). 
Do NOT write an essay. Output the JSON immediately.
"""
    try:
        from src.clients.llm_client import nvidia_news_model, query_local_llm

        # Pass headline/summary as JSON user prompt. Use the text-only GLM 5.2
        # NVIDIA model for cheap news summarization (no vision needed here).
        user_prompt = json.dumps({"headline": headline, "summary": summary})

        response_text = query_local_llm(
            system_prompt=prompt,
            user_prompt=user_prompt,
            json_mode=True,
            max_tokens=512,
            disable_thinking=True,
            model=nvidia_news_model(),
        )
        if response_text:
            if "</think>" in response_text:
                json_str = response_text.split("</think>")[-1].strip()
            else:
                json_str = response_text.strip()

            if json_str.startswith("```json"):
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif json_str.startswith("```"):
                json_str = json_str.split("```")[1].split("```")[0].strip()

            parsed = json.loads(json_str)

            sentiment = parsed.get("sentiment", "neutral").upper().strip()
            catalyst = parsed.get("catalyst", "none").strip()

            # Standardize outputs
            if sentiment not in ["BULLISH", "BEARISH", "NEUTRAL"]:
                sentiment = "NEUTRAL"

            logger.info(f"Local LLM parsed sentiment: {sentiment} | Catalyst: {catalyst}")
            return parsed

        raise Exception("Empty response from local LLM")
    except Exception as e:
        logger.warning(
            f"Local LLM sentiment query failed or timed out: {e}. Falling back to lexicon scoring."
        )

    return analyze_sentiment_deterministically(headline, summary)


def _fetch_finnhub_news(symbol: str, days: int = 2) -> str:
    """Fetch recent company news from Finnhub and return formatted context text.

    Delegates to the shared, rate-limited finnhub_client. Returns "" if no key,
    no articles, or on error so callers can fall back to Alpaca/Yahoo.
    """
    try:
        from src.clients.finnhub_client import get_ticker_news_context

        return get_ticker_news_context(symbol, days=days)
    except Exception as e:
        logger.warning(f"Finnhub news fetch failed for {symbol}: {e}")
        return ""


def get_ticker_news(symbol: str, days: int = 2) -> Dict[str, Any]:
    """
    Fetch the latest news article for a given ticker symbol using Alpaca News API.
    Returns a dictionary of deterministic metrics.
    """
    from datetime import datetime, timedelta

    symbol = symbol.upper().strip()
    default_result = {
        "url": "",
        "source": "",
        "catalyst": "No recent news found",
        "catalyst_type": "none",
        "sentiment": "NEUTRAL",
        "corroborates_direction": False,
        "event_flags": [],
        "as_of": datetime.now().strftime("%Y-%m-%d"),
    }

    # Bypassing Alpaca News for indices since they don't have company-specific news
    if symbol in ["SPX", "VIX", "COMP"]:
        return default_result

    if not ALPACA_KEY or not ALPACA_SECRET:
        logger.warning("Alpaca keys are not configured. Bypassing news fetch.")
        return default_result

    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    url = f"{DATA_HOST}/v1beta1/news"

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    # 0. Try Finnhub company-news first (free tier, already a project dependency)
    finnhub_ctx = _fetch_finnhub_news(symbol, days)
    if finnhub_ctx:
        logger.info(f"Using Finnhub news for {symbol}.")
        parsed_json = analyze_sentiment_local_llm(finnhub_ctx, "", days=days)
        if type(parsed_json) is dict:
            parsed_json["raw_news"] = finnhub_ctx
            return parsed_json
        sentiment, catalyst = parsed_json
        default_result["sentiment"] = sentiment
        default_result["catalyst"] = catalyst
        default_result["raw_news"] = finnhub_ctx
        return default_result

    try:
        logger.info(f"Fetching recent {days} days of news for {symbol} from Alpaca News API...")

        # Alpaca supports start/end in RFC3339, but we can also just fetch a larger limit and filter
        params = {
            "symbols": symbol,
            "start": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": 15,
        }

        response = requests.get(url, headers=headers, params=params, timeout=5)
        if response.status_code != 200:
            logger.warning(f"Alpaca News API returned code {response.status_code}: {response.text}")

        data = response.json() if response.status_code == 200 else {}
        articles = data.get("news", [])

        news_context = ""
        first_url = ""
        first_source = ""
        if articles:
            for art in articles:
                if not first_url:
                    first_url = art.get("url", "") or ""
                    first_source = art.get("source", "") or ""
                date_str = art.get("created_at", "")[:10]
                news_context += (
                    f"[{date_str}] {art.get('headline', '')}\nSummary: {art.get('summary', '')}\n\n"
                )
        else:
            logger.info(f"No news from Alpaca for {symbol}. Attempting Yahoo Finance fallback...")
            import yfinance as yf

            ticker_obj = yf.Ticker(symbol)
            yf_news = ticker_obj.news
            if yf_news:
                for art in yf_news:
                    pub_time = art.get("providerPublishTime", 0)
                    # Convert unix timestamp to YYYY-MM-DD
                    date_str = (
                        datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d") if pub_time else ""
                    )
                    news_context += f"[{date_str}] {art.get('title', '')}\nPublisher: {art.get('publisher', '')}\n\n"

        if not news_context:
            logger.info(f"No news articles returned for {symbol} from Alpaca or Yahoo.")
            return default_result

        # Query local LLM for sentiment/catalyst classification
        parsed_json = analyze_sentiment_local_llm(news_context, "", days=days)
        if type(parsed_json) is dict:
            logger.info(f"Successfully processed recent news for {symbol}")
            parsed_json["raw_news"] = news_context
            parsed_json["url"] = first_url
            parsed_json["source"] = first_source
            return parsed_json
        else:
            # Fallback to deterministic if LLM failed and returned tuple
            sentiment, catalyst = parsed_json
            default_result["sentiment"] = sentiment
            default_result["catalyst"] = catalyst
            default_result["raw_news"] = news_context
            default_result["url"] = first_url
            default_result["source"] = first_source
            return default_result

    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")
        return default_result
