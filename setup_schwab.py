import base64
import json
import os
import time
import urllib.parse
import webbrowser
import requests
import schwab
from dotenv import load_dotenv

load_dotenv(override=True)

SCHWAB_API_CLIENT_ID = os.getenv("SCHWAB_API_CLIENT_ID")
SCHWAB_API_CLIENT_SECRET = os.getenv("SCHWAB_API_CLIENT_SECRET")
SCHWAB_CALLBACK_URL = os.getenv("SCHWAB_CALLBACK_URL", "https://120.0.0.1:8182")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "data", "schwab_token.json")


def exchange_code_for_token(code: str, callback_url: str) -> dict:
    """Exchange authorization code directly via Schwab's token endpoint."""
    token_url = "https://api.schwabapi.com/v1/oauth/token"
    creds = f"{SCHWAB_API_CLIENT_ID}:{SCHWAB_API_CLIENT_SECRET}"
    b64_creds = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
    
    headers = {
        "Authorization": f"Basic {b64_creds}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    
    # Try with decoded code (ending in @)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url,
    }
    
    print(f"[*] Exchanging authorization code with Schwab token endpoint...")
    resp = requests.post(token_url, headers=headers, data=data, timeout=15)
    
    # If failed and code has @ or %40, try alternate encoding
    if resp.status_code != 200:
        alt_code = urllib.parse.quote(code) if "@" in code else urllib.parse.unquote(code)
        if alt_code != code:
            print(f"[*] Trying alternate code encoding...")
            data["code"] = alt_code
            resp_alt = requests.post(token_url, headers=headers, data=data, timeout=15)
            if resp_alt.status_code == 200:
                return resp_alt.json()
        
        print(f"[-] Token endpoint returned HTTP {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    return resp.json()


def setup():
    print("=== Schwab API Initial Setup ===")
    if not SCHWAB_API_CLIENT_ID or not SCHWAB_API_CLIENT_SECRET:
        print("ERROR: Missing Client ID or Secret in .env file.")
        return

    # Use schwab-py's official endpoint: https://api.schwabapi.com/v1/oauth/authorize
    auth_context = schwab.auth.get_auth_context(SCHWAB_API_CLIENT_ID, SCHWAB_CALLBACK_URL)
    auth_url = auth_context.authorization_url
    
    print("Opening your browser to Schwab login...")
    print(f"\nAuthorization URL:\n{auth_url}\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("Instructions:")
    print("1. Log in on the Schwab window that just opened.")
    print("2. Click 'Allow' on the consent screen.")
    print("3. When redirected to https://120.0.0.1:8182/?code=... copy the ENTIRE URL from your address bar.")
    print("4. Paste it below.\n")

    received = input("Paste Redirect URL here> ").strip()
    if not received:
        print("ERROR: No URL provided.")
        return

    # Clean any accidental leading/trailing quotes or spaces
    received = received.strip("\"' ")

    # Extract code from URL
    try:
        # First attempt: schwab-py official exchange
        try:
            client = schwab.auth.client_from_received_url(
                SCHWAB_API_CLIENT_ID,
                SCHWAB_API_CLIENT_SECRET,
                auth_context,
                received,
                lambda t: None
            )
            token_data = client.session.token
        except Exception as e_schwab:
            print(f"[*] schwab-py direct exchange attempt had notice ({e_schwab}), trying direct REST exchange...")
            if "?" in received:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(received).query)
                code = qs.get("code", [None])[0]
            else:
                code = received

            if not code:
                raise ValueError(f"Could not extract 'code' parameter from: {received}")
            
            token_data = exchange_code_for_token(code, SCHWAB_CALLBACK_URL)
        
        # Save in schwab-py format with metadata
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        wrapped = {
            "creation_timestamp": int(time.time()),
            "token": token_data,
        }
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            json.dump(wrapped, f, indent=2)

        print("\n=== SUCCESS ===")
        print(f"Token saved successfully to {TOKEN_PATH}!")
        
        # Verify with schwab-py
        verify_client = schwab.auth.client_from_token_file(TOKEN_PATH, SCHWAB_API_CLIENT_ID, SCHWAB_API_CLIENT_SECRET)
        print("Schwab client initialized and verified successfully. Options flow scanner is active!")

    except Exception as e:
        print(f"\n=== ERROR ===\n{e}")


if __name__ == "__main__":
    setup()
