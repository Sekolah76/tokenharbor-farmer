#!/usr/bin/env python3
"""th_export.py — Export akun ke 2 file:
1. accounts_full.txt  : email | password | api_key (tiap baris)
2. api_keys.txt       : api_key (satu per baris)

Dari th_tor_state.json (atau file lain via arg).
Usage: python th_export.py [state_file]
"""
import os, sys, json

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    state_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "th_tor_state.json")
    full_out = os.path.join(here, "accounts_full.txt")
    keys_out = os.path.join(here, "api_keys.txt")

    if not os.path.exists(state_file):
        print(f"❌ State tidak ada: {state_file}")
        return 1

    st = json.load(open(state_file))
    accts = st.get("accounts", [])
    if not accts:
        print("❌ Tidak ada akun di state")
        return 1

    with open(full_out, "w") as f1, open(keys_out, "w") as f2:
        for a in accts:
            email = a.get("email", "")
            pw = a.get("password", "")
            key = a.get("api_key", "")
            if key:
                f1.write(f"{email} | {pw} | {key}\n")
                f2.write(f"{key}\n")

    n = len([a for a in accts if a.get("api_key")])
    print(f"✅ Export {n} akun:")
    print(f"  📄 {full_out}  (email | password | api_key)")
    print(f"  🔑 {keys_out}   (api_key saja)")
    return 0

if __name__ == "__main__":
    sys.exit(main())