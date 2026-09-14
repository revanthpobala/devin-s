import sqlite3
conn = sqlite3.connect('data/research_watch.db')
c = conn.cursor()
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('Tables:', tables)
if 'suggested_trades_audit' in tables:
    cols = [r[1] for r in c.execute("PRAGMA table_info(suggested_trades_audit)").fetchall()]
    print('suggested_trades_audit cols:', cols)
    count = c.execute("SELECT COUNT(*) FROM suggested_trades_audit").fetchone()[0]
    print('suggested_trades_audit rows:', count)
else:
    print('suggested_trades_audit NOT found!')
