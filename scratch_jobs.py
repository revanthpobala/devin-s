import sqlite3, json

conn = sqlite3.connect('data/research_watch.db')
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT job_id, stage, stage_detail, status, error_message, started_at, completed_at FROM active_research_jobs WHERE ticker='AAPL' ORDER BY started_at DESC LIMIT 3").fetchall()
for r in rows:
    print(json.dumps(dict(r), indent=2))
