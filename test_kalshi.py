from dotenv import load_dotenv
load_dotenv()
import requests
from src.clients.kalshi_client import _get_auth_headers, KALSHI_API_URL

headers = _get_auth_headers('GET', '/trade-api/v2/events?status=open&limit=1000')
res = requests.get(f'{KALSHI_API_URL}/events?status=open&limit=1000', headers=headers)
events = res.json().get('events', [])
matches = []
for e in events:
    title = e.get('title', '').lower()
    if 's&p' in title or 'spx' in title or 'close' in title or 'stock' in title:
        matches.append(e.get('title'))

print('--- INDEX MARKETS ON KALSHI ---')
for t in list(set(matches)): print(f'- {t}')
