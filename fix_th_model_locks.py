#!/usr/bin/env python3
"""fix_th_model_locks.py — Tambah modelLock qwen3.8-27b:free ke SEMUA conn th baru (TH th106+).
Juga pastikan modelLock mimo + deepseek ada. Pakai buat qwen free muncul di 9router.
"""
import sqlite3, json, os, db_path

DB = db_path.find_9router_db()
FREE_MODELS = ["mimo-v2.5:free", "deepseek-v4-flash:free", "qwen3.8-27b:free"]

c = sqlite3.connect(DB)
# semua conn TH (tokenharbor)
rows = c.execute("SELECT id, name, data FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchall()
updated = 0
for nid, name, data_str in rows:
    d = json.loads(data_str)
    changed = False
    for m in FREE_MODELS:
        lock_key = f"modelLock_{m}"
        if lock_key not in d:
            d[lock_key] = 1
            changed = True
    if changed:
        c.execute("UPDATE providerConnections SET data=? WHERE id=?", (json.dumps(d), nid))
        updated += 1
c.commit()
print(f"Updated {updated} conn dengan modelLock qwen (dan lainnya)")
# verifikasi
r2 = c.execute("SELECT data FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' LIMIT 1").fetchone()
d2 = json.loads(r2[0])
print("Sample modelLock:", [k for k in d2 if k.startswith('modelLock')])
c.close()