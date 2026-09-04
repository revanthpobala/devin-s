import json
import os
import sys
import urllib.parse
import requests
from dotenv import load_dotenv

load_dotenv()

# REMINDER: User rules strictly forbid logging or printing secret values!
CLIENT_ID = (os.getenv("TASTY_TRADE_CLIENT_ID") or os.getenv("TASTYTRADE_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("TASTY_TRADE_CLIENT_SECRET") or os.getenv("TASTYTRADE_CLIENT_SECRET") or "").strip()
CALLBACK_URL = (os.getenv("TASTY_TRADE_CALLBACK_URL") or os.getenv("TASTYTRADE_CALLBACK_URL") or "http://localhost:8000").strip()
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "data", "tastytrade_token.json")


def setup():
    print("=== Tastytrade API OAuth Setup ===")
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: TASTY_TRADE_CLIENT_ID or TASTY_TRADE_CLIENT_SECRET is missing in .env")
        return

    print("Choose an option:")
    print("1. Paste Refresh Token directly (If you clicked 'Create Grant / Generate Token' in Tastytrade dashboard)")
    print("2. Browser Authorization Flow (Log in and approve)")
    choice = input("\nEnter choice [1 or 2, default: 1]: ").strip() or "1"

    if choice == "1":
        refresh_token = input("\nPaste your Tastytrade Refresh Token: ").strip()
        if not refresh_token:
            print("Error: No token provided.")
            return

        print("\nVerifying Refresh Token and generating initial access token...")
        token_url = "https://api.tastyworks.com/oauth/token"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "StockResearchAlerts/1.0",
        }
        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": refresh_token,
        }
        try:
            resp = requests.post(token_url, json=payload, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"Failed to verify token (HTTP {resp.status_code}): {resp.text}")
                return

            token_data = resp.json()
            token_data["refresh_token"] = token_data.get("refresh_token") or refresh_token
            token_data["created_at"] = os.path.getmtime(TOKEN_PATH) if os.path.exists(TOKEN_PATH) else None
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            with open(TOKEN_PATH, "w", encoding="utf-8") as f:
                json.dump(token_data, f, indent=2)

            print("\n=== SUCCESS ===")
            print(f"Tastytrade OAuth tokens successfully verified and saved to {TOKEN_PATH}!")
            return
        except Exception as e:
            print(f"Error: {e}")
            return

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": CALLBACK_URL,
    }
    auth_url = f"https://my.tastytrade.com/auth.html?{urllib.parse.urlencode(params)}"

    print("\n1. Click or open this authorization URL in your browser:")
    print(f"\n   {auth_url}\n")
    print("2. Log in to Tastytrade and click 'Authorize / Approve'.")
    print(f"3. Your browser will redirect to an address like: {CALLBACK_URL}/?code=AUTH_CODE")
    print("4. Copy the ENTIRE URL (or just the code value) and paste it below:\n")

    redirected_url = input("Paste redirected URL (or code): ").strip()
    if not redirected_url:
        print("Error: No URL provided.")
        return

    # Extract code
    code = redirected_url
    if "code=" in redirected_url:
        parsed = urllib.parse.urlparse(redirected_url)
        q = urllib.parse.parse_qs(parsed.query)
        if "code" in q:
            code = q["code"][0]

    print("\nExchanging authorization code for OAuth tokens...")
    token_url = "https://api.tastyworks.com/oauth/token"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "StockResearchAlerts/1.0",
    }
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": CALLBACK_URL,
    }

    try:
        resp = requests.post(token_url, json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"Failed to obtain token (HTTP {resp.status_code}): {resp.text}")
            return

        token_data = resp.json()
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(token_data, f, indent=2)

        print("\n=== SUCCESS ===")
        print(f"Tastytrade OAuth token successfully generated and saved to {TOKEN_PATH}!")
        print("Your Tastytrade Quote Alerts client is now fully authenticated.")
    except Exception as e:
        print(f"OAuth setup error: {e}")


if __name__ == "__main__":
    setup()
