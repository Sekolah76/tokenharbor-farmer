#!/usr/bin/env python3
"""th_preflight.py — Pre-flight check sebelum farm. Deteksi error lebih awal.
Cek: Python deps, Tor (9050+9051), env DB, state validity, 9router reachable.
Exit 0 = OK siap farm, exit 1 = ada masalah (tampilkan fix).
"""
import os, sys, json, socket, sqlite3

def check(ok, msg, fix=""):
    tag = "✅" if ok else "❌"
    print(f"  {tag} {msg}")
    if not ok and fix:
        print(f"     → Fix: {fix}")
    return ok

def main():
    print("=== TOKENHARBOR FARMER — PRE-FLIGHT CHECK ===\n")
    all_ok = True
    here = os.path.dirname(os.path.abspath(__file__))

    # 1. Python deps
    print("[1] Dependencies")
    deps = [("requests", "requests"), ("pysocks", "socks"), ("rich", "rich")]
    for name, mod in deps:
        try:
            __import__(mod)
            all_ok &= check(True, f"{name} ✓")
        except ImportError:
            all_ok &= check(False, f"{name} TIDAK ada", f"pip install {name}")
    print()

    # 2. Tor
    print("[2] Tor")
    def _port_open(p):
        try:
            s = socket.create_connection(("127.0.0.1", p), timeout=3); s.close(); return True
        except Exception:
            return False
    socks = _port_open(9050)
    ctrl = _port_open(9051)
    all_ok &= check(socks, "Socks 9050 (proxy)", "jalankan: tor -f torrc")
    all_ok &= check(ctrl, "Control 9051 (rotation)", "tambah ControlPort 9051 di torrc")
    print()

    # 3. State
    print("[3] State")
    state_file = os.path.join(here, "th_tor_state.json")
    if os.path.exists(state_file):
        try:
            st = json.load(open(state_file))
            n = len(st.get("accounts", []))
            all_ok &= check(True, f"State valid — {n} akun tersimpan")
        except Exception as e:
            all_ok &= check(False, f"State rusak: {str(e)[:50]}", "hapus/backup th_tor_state.json")
    else:
        all_ok &= check(True, "Belum ada state (fresh start — OK)")
    print()

    # 4. 9Router DB
    print("[4] 9Router DB")
    db = os.environ.get("NINE_ROUTER_DB", r"C:\Users\Arsyad\AppData\Roaming\9router\db\data.sqlite")
    if os.path.exists(db):
        try:
            c = sqlite3.connect(db)
            n = c.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%'").fetchone()[0]
            c.close()
            all_ok &= check(True, f"DB OK — {n} conn tokenharbor")
        except Exception as e:
            all_ok &= check(False, f"DB error: {str(e)[:50]}")
    else:
        all_ok &= check(False, f"DB tidak ditemukan: {db}", "set NINE_ROUTER_DB atau jalankan --no-inject")
    print()

    print("=" * 50)
    if all_ok:
        print("  ✅ SEMUA OK — siap farm!")
        print("  Jalankan: python th_tui.py  (menu visual)")
        print("        atau  python th_tor_farm.py 100")
    else:
        print("  ⚠️ ADA MASALAH — perbaiki di atas sebelum farm")
    print("=" * 50)
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())