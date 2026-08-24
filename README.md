# 🏭 TokenHarbor Farmer

> **Auto-register akun TokenHarbor + enable free models + inject ke 9Router — bypass IP rate-limit via TOR, verified 100 akun.**

Tool CLI untuk membuat akun [TokenHarbor](https://tokenharbor.ai) secara massal/otomatis:
1. **Register** via Next.js Server Action (tembus `signup_ip_required`)
2. **Verify email** otomatis (temp mail poll + auto-click link)
3. **Enable free models** (klik consent `free_models_enabled`)
4. **Buat API key** — 1 akun = 1 key (auto-cleanup key bawaan)
5. **Test key** — call `mimo-v2.5:free`
6. **Inject ke 9Router** — conn baru, GABUNG (tidak hapus key lama)

**Bypass "Too many sign-ups from this network"** dengan **TOR exit node rotation** (gratis, unlimited IP — NEWNYM per circuit).

---

## 📦 Fitur

| Fitur | Status |
|---|---|
| **Rich TUI menu (klik 1/2/3 visual)** | ✅ |
| Auto-register (Next.js Server Action) | ✅ |
| TOR IP rotation (bypass rate-limit) | ✅ |
| Verify email otomatis | ✅ |
| Enable free models | ✅ |
| 1 akun = 1 API key | ✅ |
| Test key (`mimo-v2.5:free`) | ✅ |
| Inject 9Router (gabung, no-delete) | ✅ |
| Resume dari crash (state JSON) | ✅ |
| Loop register→logout | ✅ |
| Batch massal | ✅ |

---

## 🧠 Model yang Tersedia (TokenHarbor)

Setelah farm, akun punya akses ke **free tier** ini (via 9Router: `th/<model>`):

| Model Free (`:free`) | Deskripsi |
|---|---|
| `th/deepseek-v4-flash:free` | DeepSeek V4 Flash — cepat, murah, reasoning |
| `th/mimo-v2.5:free` | MiMo V2.5 — general chat |
| `th/qwen3.8-27b:free` | Qwen 3.8 27B — open-weight |

**Model premium** (akun free TIDAK bisa akses — butuh balance/plan):
`th/gpt-5.6-luna` · `th/gpt-5.6-sol` · `th/gpt-5.6-terra` · `th/claude-opus-5` · `th/claude-sonnet-5` · `th/claude-fable-5` · `th/gemini-3.7-flash` · `th/grok-4.6` · `th/deepseek-v4-pro` · `th/glm-5.3` · `th/kimi-k3` · `th/mimo-v2.5-pro` · `th/qwen3.8-max` · `th/th-orchestra`

---

## 📋 Persyaratan

| Requirement | Keterangan |
|---|---|
| Python 3.10+ | `pip install requests pysocks` |
| Tor | [tor-expert-bundle](https://www.torproject.org/download/tor/) — SocksPort 9050 + ControlPort 9051 |
| 9Router (opsional) | untuk inject — path DB via env `NINE_ROUTER_DB` |
| Supabase anon (opsional) | untuk logout — env `TH_ANON_KEY` |

---

## 🚀 Cara Clone & Pakai (Terminal)

### 1. Clone repo
```bash
git clone https://github.com/Sekolah76/tokenharbor-farmer.git
cd tokenharbor-farmer
```

### 2. Install dependency
```bash
pip install requests pysocks
```

### 3. Setup Tor
```bash
# Download tor-expert-bundle untuk OS kamu:
#   https://www.torproject.org/download/tor/
# Buat file torrc:
mkdir -p ~/tor && cat > ~/tor/torrc <<'EOF'
SocksPort 9050
ControlPort 9051
CookieAuthentication 0
DataDirectory ~/tor/data
EOF
# Jalankan:
tor -f ~/tor/torrc
```

### 4. Konfigurasi (opsional)
```bash
export NINE_ROUTER_DB="C:/path/ke/9router/data.sqlite"   # wajib kalau mau inject
export TH_ANON_KEY="..."                                  # Supabase anon (untuk logout)
```

### 5. Jalankan
```bash
# 🎨 MODE VISUAL (rekomendasi) — menu klik-klik 1/2/3:
python th_tui.py

# ⚙️ Mode CLI langsung:
# Register 1 akun (cek alur)
python th_auto_register.py single

# Farm 100 akun (auto-inject 9router, resume-able)
python th_tor_farm.py 100

# Farm 50 akun tanpa inject
python th_tor_farm.py 50 --no-inject

# Loop register→logout
python th_auto_register.py loop 10

# Tambahkan kv customModels ke 9router (biar model th muncul)
python inject_th_kv.py

# (Jika model free qwen tidak muncul) tambahkan modelLock ke semua conn th:
python fix_th_model_locks.py

# (Jika conn baru tidak muncul di UI 9router — provider beda) fix provider:
python fix_th_provider.py
```

### 6. Verifikasi
```bash
# Test 1 key (masukkan key-nya)
curl https://tokenharbor.ai/v1/chat/completions \
  -H "Authorization: Bearer thk_live_..." \
  -d '{"model":"mimo-v2.5:free","messages":[{"role":"user","content":"hi"}]}'

# Atau via 9Router (kalau sudah inject)
curl http://127.0.0.1:20128/v1/chat/completions \
  -H "Authorization: Bearer <9ROUTER_KEY>" \
  -d '{"model":"th/deepseek-v4-flash:free","messages":[{"role":"user","content":"hi"}]}'
```

---

## 🤖 Cara Pakai dari Agent (Claude Code / opencode / Hermes)

Taruh file ini di project agent, lalu instruct:

```
Use th_tor_farm.py to farm N TokenHarbor accounts.
Workflow: register → verify email → enable free models → create API key (1 per account) → inject to 9Router (merge, don't delete old).
Report: count, verified count, injected count.
```

Script bisa dipanggil langsung:
```bash
python /path/to/th_tor_farm.py 20          # agent farming
python /path/to/th_auto_register.py single # agent 1 akun
```

---

## 🧪 Hasil Verified (Agt 2026)

- **100 akun** berhasil dibuat — SEMUA: verified email ✅, free models enabled ✅, key test 200 ✅, inject 9Router ✅
- **3 model free per akun**: `mimo-v2.5:free`, `deepseek-v4-flash:free`, `qwen3.8-27b:free` — semua 200 OK (verified langsung + via 9Router)
- **9Router: 205 conn th aktif** (111 key lama + 100 farm baru) — semua di node `TokenHarbor` yang sama, aktif & dipakai
- Rate: ~1 akun / 1-3 menit (depend Tor)
- 100% akun aktif & bisa dipakai via 9Router (`th/deepseek-v4-flash:free` = 200 OK)
- Grace period free tier: **7 hari per akun** sejak enable — farm massal = stock berlapis

---

## 📁 Struktur File

```
tokenharbor-farmer/
├── th_tui.py               # 🎨 Rich TUI menu (klik 1/2/3 visual)
├── th_tor_farm.py          # Farm massal (resume, auto-rotate, inject)
├── th_auto_register.py     # CLI: single / loop register→logout
├── inject_th_kv.py         # Inject kv customModels ke 9Router
├── fix_th_model_locks.py   # Tambah modelLock free (qwen dll) ke semua conn th
├── fix_th_provider.py      # Fix provider conn (gabung ke node TokenHarbor yg benar)
├── STEPBYSTEP.md           # Full technical workflow (endpoints, action IDs, error handling)
├── README.md               # Ini
└── .gitignore              # Exclude state, keys, env
```

---

## 📖 Detail Teknis

Baca **[STEPBYSTEP.md](STEPBYSTEP.md)** untuk:
- Endpoint signup + multipart body (Next.js Server Action)
- Action ID & headers yang dibutuhkan
- Error handling (`Too many`, `couldn't create`, `human check`)
- Setup Tor + NEWNYM rotation
- Skema inject 9Router

---

## ⚠️ Troubleshooting

| Masalah | Solusi |
|---|---|
| `Too many sign-ups from this network` | IP burn — pastikan Tor jalan, script auto-rotate NEWNYM. Jangan pakai IP rumah/WebShare/WARP. |
| `We couldn't create your account` | Exit node Tor di-flag — auto-rotate (script handle). |
| `Please complete the human check` | Turnstile — rotate circuit (script handle). |
| Chat 403 `Verify your email address` | Email belum verified — script verify otomatis; pastikan tidak timeout. |
| Tor tidak connect | Cek torrc (SocksPort 9050, ControlPort 9051) + tor jalan. |
| Injection gagal | Set `NINE_ROUTER_DB` dengan path yang benar. |

---

## ⚠️ Catatan & Tanggung Jawab

- **Gunakan untuk**: akun & workflow milik sendiri, automation testing.
- **1 akun = 7 hari free** sejak enable — setelah itu free tier berakhir (butuh akun baru).
- **TIDAK untuk**: abuse layanan pihak ketiga, pembajakan, atau aktivitas ilegal.
- Tanggung jawab penggunaan = pengguna.

---

## 📜 License

MIT — bebas pakai, modifikasi, distribusi. Attribution optional.

© 2026 TokenHarbor Farmer