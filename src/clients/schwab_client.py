import json
import logging
import os
from datetime import datetime, timedelta

import schwab
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load env in case it's run independently
load_dotenv()

SCHWAB_API_CLIENT_ID = os.getenv("SCHWAB_API_CLIENT_ID")
SCHWAB_API_CLIENT_SECRET = os.getenv("SCHWAB_API_CLIENT_SECRET")
SCHWAB_CALLBACK_URL = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "schwab_token.json")

def get_schwab_client():
    if not SCHWAB_API_CLIENT_ID or not SCHWAB_API_CLIENT_SECRET:
        raise ValueError("SCHWAB_API_CLIENT_ID or SCHWAB_API_CLIENT_SECRET is missing in .env")
        
    try:
        client = schwab.auth.client_from_token_file(
            TOKEN_PATH, 
            SCHWAB_API_CLIENT_ID, 
            SCHWAB_API_CLIENT_SECRET
        )
        return client
    except FileNotFoundError:
        raise Exception("schwab_token.json not found. Please run python setup_schwab.py first.")

def get_unusual_options_flow(ticker: str) -> str:
    """
    Fetches the option chain for the next 90 days and scans for unusual
    options activity (Volume > OI * 1.5 and Volume > 500).
    """
    try:
        client = get_schwab_client()
    except Exception as e:
        logger.error(f"Failed to initialize Schwab client: {e}")
        return "Error: Schwab API client is not authenticated. Please run the setup script."

    # Define the 90-day window
    now = datetime.now()
    to_date = now + timedelta(days=90)
    
    try:
        r = client.get_option_chain(
            ticker,
            contract_type=client.Options.ContractType.ALL,
            from_date=now.date(),
            to_date=to_date.date()
        )
        if r.status_code != 200:
            logger.error(f"Schwab API returned {r.status_code}: {r.text}")
            return f"Error: Schwab API returned {r.status_code}"
            
        data = r.json()
    except Exception as e:
        logger.error(f"Failed to fetch option chain from Schwab: {e}")
        return f"Error: {e}"

    if data.get("status") != "SUCCESS":
        return f"Error: Schwab API returned status {data.get('status')}"

    anomalies = []
    
    # Process Calls
    call_map = data.get("callExpDateMap", {})
    for exp_date, strikes in call_map.items():
        for strike, contracts in strikes.items():
            for contract in contracts:
                vol = contract.get("totalVolume", 0)
                oi = contract.get("openInterest", 0)
                # Filter for unusual flow
                if vol >= 500 and vol > (oi * 1.5):
                    anomalies.append({
                        "Type": "CALL",
                        "Strike": float(strike),
                        "Expiry": exp_date.split(":")[0],
                        "Volume": vol,
                        "OI": oi,
                        "Delta": round(contract.get("delta", 0), 3),
                        "Mid": round((contract.get("bid", 0) + contract.get("ask", 0)) / 2, 2)
                    })
                    
    # Process Puts
    put_map = data.get("putExpDateMap", {})
    for exp_date, strikes in put_map.items():
        for strike, contracts in strikes.items():
            for contract in contracts:
                vol = contract.get("totalVolume", 0)
                oi = contract.get("openInterest", 0)
                # Filter for unusual flow
                if vol >= 500 and vol > (oi * 1.5):
                    anomalies.append({
                        "Type": "PUT",
                        "Strike": float(strike),
                        "Expiry": exp_date.split(":")[0],
                        "Volume": vol,
                        "OI": oi,
                        "Delta": round(contract.get("delta", 0), 3),
                        "Mid": round((contract.get("bid", 0) + contract.get("ask", 0)) / 2, 2)
                    })

    if not anomalies:
        return f"No unusual options flow detected for {ticker} in the next 90 days."

    # Sort by Volume descending to highlight the biggest block sweeps
    anomalies.sort(key=lambda x: x["Volume"], reverse=True)
    
    # Take top 15 anomalies to not blow up LLM token limits
    anomalies = anomalies[:15]

    output = [f"### Unusual Options Flow for {ticker} (Next 90 Days)"]
    output.append("| Type | Strike | Expiry | Volume | OI | Delta | Mid Price |")
    output.append("|------|--------|--------|--------|----|-------|-----------|")
    
    for a in anomalies:
        output.append(f"| {a['Type']} | ${a['Strike']} | {a['Expiry']} | **{a['Volume']}** | {a['OI']} | {a['Delta']} | ${a['Mid']} |")
        
    output.append("\n*Note: Volume > Open Interest * 1.5 indicates massive institutional positioning initiated today.*")
    
    return "\n".join(output)

if __name__ == "__main__":
    # Test script block
    print("Testing Schwab Client...")
    print(get_unusual_options_flow("AAPL"))
