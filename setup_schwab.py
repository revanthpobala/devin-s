import os
import schwab
from dotenv import load_dotenv

load_dotenv()

SCHWAB_API_CLIENT_ID = os.getenv("SCHWAB_API_CLIENT_ID")
SCHWAB_API_CLIENT_SECRET = os.getenv("SCHWAB_API_CLIENT_SECRET")
SCHWAB_CALLBACK_URL = os.getenv("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "data", "schwab_token.json")

def setup():
    print("=== Schwab API Initial Setup ===")
    print("The script will now attempt to authenticate with Charles Schwab.")
    print("1. A URL will be printed below.")
    print("2. Click the link and log in to your Schwab / ThinkOrSwim account.")
    print("3. Agree to the terms.")
    print("4. You will be redirected to an empty page (e.g. https://127.0.0.1/?code=...).")
    print("5. Copy the ENTIRE URL from your browser's address bar and paste it back into this terminal.")
    print("================================\n")
    
    if not SCHWAB_API_CLIENT_ID or not SCHWAB_API_CLIENT_SECRET:
        print("ERROR: Missing Client ID or Secret in .env file.")
        return

    try:
        client = schwab.auth.client_from_manual_flow(
            api_key=SCHWAB_API_CLIENT_ID,
            app_secret=SCHWAB_API_CLIENT_SECRET,
            callback_url=SCHWAB_CALLBACK_URL,
            token_path=TOKEN_PATH
        )
        print("\n=== SUCCESS ===")
        print("token.json generated successfully. The options flow scanner is ready!")
    except Exception as e:
        print(f"\n=== ERROR ===\n{e}")

if __name__ == "__main__":
    setup()
