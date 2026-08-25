#!/usr/bin/env python3
"""fix_th_provider.py — Fix provider conn th yang salah ('openai-compatible')
agar GABUNG ke node TokenHarbor yang benar (auto-detect dari DB).

Auto-detect: ambil provider id dari conn tokenharbor existing yang benar.
Kalau tidak ada conn existing → buat node baru dengan id unik (uuid).
"""
import sqlite3
import os
import uuid

DB = os.environ.get("NINE_ROUTER_DB", None)
if not DB:
    # fallback generic: cari DB 9router secara lokal (default lokasi umum)
    for cand in [
        os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite"),
        "/app/data/data.sqlite",
        os.path.expanduser("~/.9router/data.sqlite"),
    ]:
        if os.path.exists(cand):
            DB = cand
            break
    else:
        DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")

c = sqlite3.connect(DB)
# ambil provider lama dari 1 conn lama (yang bukan 'openai-compatible')
old = c.execute("SELECT DISTINCT provider, authType FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' AND provider != 'openai-compatible' LIMIT 1").fetchone()
if old:
    TARGET_PROVIDER, TARGET_AUTH = old
else:
    TARGET_PROVIDER = "openai-compatible-chat-" + str(uuid.uuid4())
    TARGET_AUTH = "apikey"
print(f"Target provider: {TARGET_PROVIDER} | authType: {TARGET_AUTH}")

# update conn th baru (provider 'openai-compatible') utk th
rows = c.execute("SELECT id FROM providerConnections WHERE provider='openai-compatible' AND data LIKE '%tokenharbor.ai%'").fetchall()
print(f"Conn salah provider: {len(rows)}")
for (nid,) in rows:
    c.execute("UPDATE providerConnections SET provider=?, authType=? WHERE id=?", (TARGET_PROVIDER, TARGET_AUTH, nid))
c.commit()
print("Fixed!")

# verifikasi
after = c.execute("SELECT provider, authType, COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' GROUP BY provider, authType").fetchall()
for r in after:
    print(" ", r)
c.close()