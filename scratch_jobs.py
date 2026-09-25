import sqlite3
import json

conn = sqlite3.connect('data/research_watch.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
rows = cur.execute('SELECT * FROM active_research_jobs WHERE ticker="AAPL" ORDER BY started_at DESC LIMIT 5').fetchall()
for r in rows:
    print(json.dumps(dict(r), indent=2))
