import base64
import json
import logging
import os
import time
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization

from src import config
from src.clients.llm_client import query_local_llm

logger = logging.getLogger(__name__)

KALSHI_API_URL = "https://external-api.demo.kalshi.co/trade-api/v2"

def _get_auth_headers(method: str, path: str) -> dict:
    key_id = os.getenv("KALSHI_API_KEY_ID")
    key_path = config.BASE_DIR / "kalshi" / "tradingview.txt"
    
    if not key_id or not key_path.exists():
        logger.error("Kalshi API keys missing. Ensure KALSHI_API_KEY_ID is in .env and kalshi/tradingview.txt exists.")
        return {}

    try:
        with open(key_path, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
            )
            
        timestamp = str(int(time.time() * 1000))
        msg_string = timestamp + method + path
        
        signature = private_key.sign(
            msg_string.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        return {
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode('utf-8'),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }
    except Exception as e:
        logger.error(f"Failed to generate Kalshi RSA signature: {e}")
        return {}

def fetch_prediction_market(query: str) -> str:
    """
    Queries Kalshi active markets matching the keyword and returns the implied probability (odds).
    Returns a formatted string for the LLM.
    """
    logger.info(f"Kalshi search query: {query}")
    headers = _get_auth_headers("GET", "/trade-api/v2/events?status=open&limit=100")
    if not headers:
        return "Kalshi API authentication failed."
        
    try:
        # Step 1: Fetch active events
        res = requests.get(f"{KALSHI_API_URL}/events?status=open&limit=100", headers=headers, timeout=10)
        if res.status_code != 200:
            return f"Kalshi API Error: {res.status_code} {res.text}"
            
        events = res.json().get("events", [])
        
        # Step 2: Very basic keyword matching on the event title or ticker
        query_terms = query.lower().split()
        matched_events = []
        for e in events:
            title = e.get("title", "").lower()
            if any(term in title for term in query_terms):
                matched_events.append(e)
                
        if not matched_events:
            return f"No active Kalshi markets found matching '{query}'."
            
        # Limit to top 3 matches to avoid giant context payload
        matched_events = matched_events[:3]
        
        # Step 3: Fetch the markets for the matched events to get probabilities
        results = []
        for event in matched_events:
            series_ticker = event.get("series_ticker")
            market_path = f"/markets?event_ticker={event.get('event_ticker')}"
            m_headers = _get_auth_headers("GET", f"/trade-api/v2{market_path}")
            
            m_res = requests.get(f"{KALSHI_API_URL}{market_path}", headers=m_headers, timeout=5)
            if m_res.status_code == 200:
                markets = m_res.json().get("markets", [])
                for m in markets[:2]: # Top 2 sub-markets per event
                    title = m.get("title", event.get("title"))
                    yes_ask = m.get("yes_ask_dollars", 0.0)
                    yes_bid = m.get("yes_bid_dollars", 0.0)
                    
                    # Convert to percentage
                    try:
                        prob = (float(yes_ask) + float(yes_bid)) / 2 * 100
                    except (ValueError, TypeError):
                        prob = 0.0
                        
                    results.append(f"Market: {title} | Implied Probability: {prob:.1f}%")
            else:
                logger.error(f"Failed to fetch market odds: {m_res.status_code} {m_res.text}")

        if not results:
            return "Found events but couldn't load specific market odds."
            
        return "KALSHI PREDICTION MARKETS:\n" + "\n".join(results)
        
    except Exception as e:
        logger.error(f"Kalshi API exception: {e}")
        return f"Error querying Kalshi: {str(e)}"

def get_llm_summarized_macro_odds() -> str:
    """
    Fetches broad macroeconomic prediction markets and uses the local LLM to
    summarize the consensus in 2-3 bullet points.
    """
    logger.info("Fetching top macro prediction markets for LLM summarization...")
    headers = _get_auth_headers("GET", "/trade-api/v2/events?status=open&limit=200")
    if not headers:
        return ""
        
    try:
        res = requests.get(f"{KALSHI_API_URL}/events?status=open&limit=200", headers=headers, timeout=10)
        if res.status_code != 200:
            return ""
            
        events = res.json().get("events", [])
        macro_terms = ["fed", "rate", "inflation", "cpi", "gdp", "recession", "economy"]
        
        matched_events = []
        for e in events:
            title = e.get("title", "").lower()
            if any(term in title for term in macro_terms):
                matched_events.append(e)
                
        # Limit to 6 matches to give LLM enough context but not overflow context window
        matched_events = matched_events[:6]
        if not matched_events:
            return ""
            
        results = []
        for event in matched_events:
            market_path = f"/markets?event_ticker={event.get('event_ticker')}"
            m_headers = _get_auth_headers("GET", f"/trade-api/v2{market_path}")
            
            m_res = requests.get(f"{KALSHI_API_URL}{market_path}", headers=m_headers, timeout=5)
            if m_res.status_code == 200:
                markets = m_res.json().get("markets", [])
                for m in markets[:2]: # Top 2 sub-markets per event
                    title = m.get("title", event.get("title"))
                    yes_ask = m.get("yes_ask_dollars", 0.0)
                    yes_bid = m.get("yes_bid_dollars", 0.0)
                    try:
                        prob = (float(yes_ask) + float(yes_bid)) / 2 * 100
                    except (ValueError, TypeError):
                        prob = 0.0
                    results.append(f"- {title} (Implied Probability: {prob:.1f}%)")
                    
        if not results:
            return ""
            
        raw_odds = "\n".join(results)
        logger.info(f"Routing {len(results)} Kalshi markets to Local LLM for summarization...")
        
        sys_prompt = (
            "You are a macroeconomic analyst. Review these active Kalshi prediction markets "
            "and their implied probabilities. Summarize the market consensus regarding the economy "
            "(rates, inflation, growth) in exactly 2 to 3 concise, punchy bullet points. "
            "Focus only on the most important directional takeaways. Do not hallucinate."
        )
        
        summary = query_local_llm(
            system_prompt=sys_prompt,
            user_prompt=f"KALSHI MACRO ODDS:\n{raw_odds}",
            use_openrouter=False,
            use_tools=False,
            disable_thinking=True,
            max_tokens=300
        )
        
        if summary and not summary.startswith("Error"):
            return "Kalshi Macro Consensus (LLM Summarized):\n" + summary.strip()
            
        return ""
        
    except Exception as e:
        logger.warning(f"Failed to generate LLM Kalshi summary: {e}")
        return ""

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    print(get_llm_summarized_macro_odds())
