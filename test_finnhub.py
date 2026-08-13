import os
import sys
# Set utf-8 encoding for stdout
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv()
from src.clients.finnhub_client import format_finnhub_institutional_block

print(format_finnhub_institutional_block('AAPL'))
