#!/usr/bin/env python3
"""inject_th_kv.py — Inject kv customModels utk node tokenbor (TH farming baru).
Model: mimo-v2.5:free, deepseek-v4-flash:free (free tier TH).
1 akun = 1 conn = 1 key; node id diambil dari DB.
"""
import sqlite3, json, sys, os, db_path

DB = db_path.find_9router_db()
PREFIX = "tokenbor"
MODELS = ["mimo-v2.5:free", "deepseek-v4-flash:free", "qwen3.8-27b:free"]

def main():
    c = sqlite3.connect(DB)
    row = c.execute("SELECT id FROM providerConnections WHERE provider='tokenbor' LIMIT 1").fetchone()
    if not row:
        print("NO tokenbor node"); return
    nid = row[0]
    print(f"Node: {nid}")
    added = 0
    for m in MODELS:
        mid = f"{PREFIX}/{m}"
        key = f"{nid}|{mid}|llm"
        val = json.dumps({"providerAlias": nid, "id": mid, "type": "llm", "name": mid})
        cur = c.execute("SELECT COUNT(*) FROM kv WHERE scope='customModels' AND key=?", (key,))
        if cur.fetchone()[0] == 0:
            c.execute("INSERT INTO kv (scope, key, value) VALUES ('customModels', ?, ?)", (key, val))
            added += 1
            print(f"  + {mid}")
    c.commit(); c.close()
    print(f"Added {added} kv models utk node tokenbor")

if __name__ == "__main__":
    main()