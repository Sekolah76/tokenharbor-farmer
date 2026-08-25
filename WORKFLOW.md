# 🏭 TokenHarbor Farmer — WORKFLOW Lengkap

## 📈 Alur Kerja (Visual)

```
┌─────────────────────────────────────────────────────────────┐
│  PERSIAPAN (sekali saja)                                    │
│                                                             │
│  1. git clone https://github.com/Sekolah76/tokenharbor-farmer.git
│  2. pip install -r requirements.txt                         │
│  3. Setup Tor (torrc: SocksPort 9050, ControlPort 9051)     │
│  4. (opsional) export NINE_ROUTER_DB=...  (9router)         │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  PRE-FLIGHT CHECK                                          │
│  python th_preflight.py   → ✅ SEMUA OK siap farm          │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  🎨 MODE VISUAL (rekomendasi)                              │
│  python th_tui.py                                          │
│                                                             │
│  📋 MENU                                                   │
│  [1] Register 1 akun      → signup+verify+consent+key+inject│
│  [2] Batch Farm (N)       → farm massal + resume            │
│  [3] Loop register→logout → register, logout, ulang         │
│  [4] Test API key         → cek model free                  │
│  [5] Enable Free Models   → enable utk akun existing        │
│  [6] List Akun            → lihat akun tersimpan            │
│  [7] Status 9Router       → cek conn + model                │
│  [0] Exit                                                   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  FARM (otomatis per akun)                                  │
│                                                             │
│  1. Register (Next-Action + Tor IP)                        │
│  2. Verify email (temp mail poll)                          │
│  3. Enable free models (consent)                           │
│  4. Create API key (1 akun = 1 key)                        │
│  5. Test model (mimo-v2.5:free)                            │
│  6. Inject 9Router (gabung, no-delete)                     │
│  7. Rotate circuit (NEWNYM)                                │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  HASIL                                                     │
│  ✅ N akun verified + free models + key 200 + inject       │
│  📦 th_tor_state.json (resume-able)                        │
│  🔌 9Router: th/* model siap pakai                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start (30 detik)

```bash
# 1. Clone
git clone https://github.com/Sekolah76/tokenharbor-farmer.git
cd tokenharbor-farmer

# 2. Install
pip install -r requirements.txt

# 3. Setup Tor (file torrc):
#    SocksPort 9050
#    ControlPort 9051
#    CookieAuthentication 0
#    DataDirectory C:/path/tor-data
#    lalu: tor -f torrc

# 4. Cek kesiapan
python th_preflight.py

# 5. Farm (visual menu!)
python th_tui.py
```

---

## ⚙️ Mode CLI (tanpa TUI)

```bash
# 🚀 Farm PARALEL (multi-Tor) — rekomendasi utk cepat:
python th_farm_multi.py                 # 10 akun default, 3 Tor
python th_farm_multi.py 100             # 100 akun
python th_farm_multi.py 50 --tors 5     # 50 akun, 5 Tor (5x speed)
python th_farm_multi.py 25 --no-inject  # tanpa inject

python th_auto_register.py single           # 1 akun
python th_auto_register.py loop 5           # loop register→logout 5x
python th_tor_farm.py 100                   # farm 100 akun (resume)
python th_tor_farm.py 50 --no-inject        # tanpa inject 9router
python inject_th_kv.py                      # kv models 9router
python fix_th_model_locks.py                # modelLock free ke conn th
python fix_th_provider.py                   # fix provider gabung node
```

---

## 📁 Output

| File | Isi |
|---|---|
| `th_tor_state.json` | Semua akun + key (verified/consent/inject status) |
| `account.json` / `th_auto_results.jsonl` | Hasil per-run |
| 9Router DB | 1 conn per akun (provider TokenHarbor) |

---

## 🔑 Kunci Teknis

- **Signup**: Next.js Server Action `POST /login` multipart (action ID 6003703e...)
- **IP bypass**: TOR exit node + NEWNYM rotation (unlimited IP, gratis)
- **Free models**: `mimo-v2.5:free`, `deepseek-v4-flash:free`, `qwen3.8-27b:free`
- **Verify**: temp mail poll `api.tempmail.lol` → klik link verify
- **Inject**: conn `TH thN` — provider auto-detect dari DB 9router (gabung node existing)

---

## 🧪 Verified (Agt 2026)

```bash
# Repo ini SUDAH DIUJI NYATA — bukan klaim kosong:
✅ 100+ akun — semua verified + consent + key 200 + inject 9Router
✅ 6 akun test dari clone fresh → 9Router asli → semua aktif (key 200)
✅ N conn th di 9Router (auto-gabung node)
✅ 3 model free per akun = 200 OK
✅ Resume crash, auto-rotate, smart-pause (anti wave flag)
```