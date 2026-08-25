#!/usr/bin/env python3
"""db_path.py — Utility: resolve 9Router DB path (portable — no hardcoded user).
Priority:
1. env NINE_ROUTER_DB
2. lokasi umum 9router (Windows/macOS/Linux/Docker)
3. fallback last-resort
"""
import os

def _norm(path):
    """Normalisasi path: /c/... -> C:\\... (MSYS/POSIX ke Windows)."""
    if path and path.startswith("/") and ":" not in path[:3]:
        # /c/Users/... -> C:/Users/...
        parts = path.lstrip("/").split("/", 1)
        if len(parts) == 2 and len(parts[0]) == 1:
            return parts[0].upper() + ":/" + parts[1]
    return path

def find_9router_db():
    db = os.environ.get("NINE_ROUTER_DB", "")
    if db:
        # env DIUTAMAKAN — kalau path POSIX, normalisasi
        ndb = _norm(db)
        if os.path.exists(ndb):
            return ndb
        # env ada tapi file belum exist → tetap pakai env (bukan jatuh ke DB lain)
        return ndb
    candidates = [
        os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite"),  # Windows
        os.path.expanduser("~/.9router/db/data.sqlite"),                  # Linux/macOS
        os.path.expanduser("~/Library/Application Support/9router/db/data.sqlite"),  # macOS
        "/app/data/data.sqlite",                                           # Docker
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.sqlite"),  # lokal repo
    ]
    for cand in candidates:
        if os.path.exists(cand):
            return cand
    # return default Windows (env kosong) — user bisa set NINE_ROUTER_DB
    return os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")

if __name__ == "__main__":
    print(find_9router_db())