#!/usr/bin/env python3
"""fix_th_provider.py — Fix provider conn th baru (TH th106+) agar GABUNG ke node
TokenHarbor yang benar (provider id unik yg dipakai conn lama).

Conn lama: provider='openai-compatible-chat-52f0bc28-abb2-4d13-8bdb-b7c8d448dc90', authType='apikey'
Conn baru (salah): provider='openai-compatible', authType='api_key'
"""
import sqlite3
import os

DB = os.environ.get("NINE_ROUTER_DB", r"C:\Users\Arsyad\AppData\Roaming\9router\db\data.sqlite")
TARGET_PROVIDER = "openai-compatible-chat-52f0bc28-abb2-4d13-8bdb-b7c8d448dc90"

c = sqlite3.connect(DB)
# ambil provider lama dari 1 conn lama
old = c.execute("SELECT DISTINCT provider, authType FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' AND provider != 'openai-compatible' LIMIT 1").fetchone()
if old:
    TARGET_PROVIDER, TARGET_AUTH = old
else:
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