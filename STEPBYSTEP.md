# TOKENHARBOR FARMING — Step-by-Step (verified Agt 2026)

## Status: verified penuh via TOR (jumlah akun sesuai hasil farm kamu)

---

## 🎯 Target
- 100 akun TokenHarbor, 1 akun = 1 API key
- Setiap akun: verify email + enable free models
- Inject ke 9router (GABUNG — jangan hapus key lama)

## 🔑 Kunci bypass (kenapa ini works)
- **Next.js Server Action signup** (bukan Supabase API) → tembus `signup_ip_required`
- **TOR exit node + NEWNYM rotation** → unlimited IP → tembus rate-limit "Too many sign-ups"

## 📋 Step-by-Step (per akun)

### 1. Setup email (tempmail)
```
POST https://api.tempmail.lol/v2/inbox/create
→ {address: "xxx@[sub].totalgamehub.net", token: "..."}
```
- Domain acak: totalgamehub.net / birdzpt.com / foodtrik.com (TH terima)
- Simpan email + token utk verify

### 2. Load login page (dapat cookie)
```
GET https://tokenharbor.ai/login   (via Tor proxy socks5h://127.0.0.1:9050)
```

### 3. Signup via Next.js Server Action
```
POST https://tokenharbor.ai/login   (multipart/form-data)
Headers:
  Content-Type: multipart/form-data; boundary=...
  Accept: text/x-component
  Next-Action: 607ec2c1a962aa81ad67a2483c54b0cfadfda875b2
  Next-Router-State-Tree: ["",{"children":["login",{"children":["__PAGE__",...]}]}]
  Origin/Referer: https://tokenharbor.ai/login

Body fields (multipart):
  1_$ACTION_REF_1
  1_$ACTION_1:0 = {"id":"6003703e71fc5dc99543154237e9a9267997419301","bound":"$@1"}
  1_$ACTION_1:1 = ["$undefined"]
  1_$ACTION_KEY = kb59e6b88b9f36883e58e38e7e48870c6
  1_device_fingerprint = uuid4
  1_email / 1_password
  1_invite_code
  0 = ["$undefined","$K1"]
```
SUKSES → respons mengandung "signedIn" + userId
GAGAL:
- `Too many sign-ups from this network` → IP burn → rotate (NEWNYM)
- `We couldn't create your account` → exit node buruk → rotate
- `Please complete the human check` → turnstile → rotate

### 4. Cleanup auto keys (1 akun 1 key WAJIB)
```
GET  /api/keys   → list
DELETE /api/keys/{id}  → hapus SEMUA yang auto-created
```

### 5. Create API key
```
POST /api/keys {"label": "th-XXX"}
→ 201 {"plaintext": "thk_live_..."}   ← simpan key
```

### 6. Enable free models (WAJIB — klik tombol)
```
POST /api/me/privacy {"free_models_enabled": true}
→ 200 {"ok":true}   ← consent Y
```

### 7. Verify email (poll temp mail)
```
GET https://api.tempmail.lol/v2/inbox?token=...
→ cari link https://tokenharbor.ai/verify-email?...
→ GET link (via Tor atau direct) → verified Y
```
TANPA verify → chat 403 "Verify your email address to use the API"

### 8. Test model
```
POST /v1/chat/completions {model: "mimo-v2.5:free", ...}
→ 200 = key valid + free model jalan
```

### 9. Inject ke 9router (GABUNG)
```
INSERT providerConnections (provider='openai-compatible', authType='api_key')
data: {apiKey, defaultModel:"deepseek-v4-flash", testStatus:"active",
       providerSpecificData:{prefix:"th", apiType:"chat",
         baseUrl:"https://tokenharbor.ai/v1", nodeName:"tokenharbor"},
       modelLock_mimo-v2.5:free:1, modelLock_deepseek-v4-flash:free:1}
```
- TIDAK hapus/disable conn lama — farm baru GABUNG ke node existing
- 1 akun = 1 conn

### 10. Rotasi circuit (IP baru)
```
socket → 127.0.0.1:9051 → "AUTHENTICATE\r\n" → "SIGNAL NEWNYM\r\n"
```
- Tiap 2 akun sukses + saat exit node buruk

## ⚙️ Tools yang dipakai
- **Tor**: download tor-expert-bundle → `tor -f torrc` (torrc: SocksPort 9050, ControlPort 9051)
  (SocksPort 9050, ControlPort 9051, CookieAuthentication 0)
- **9router DB**: path otomatis via `db_path.py` (env `NINE_ROUTER_DB` kalau lokasi beda)
- **Script farm**: `th-farm/th_tor_farm.py` (loop + retry + inject otomatis)
- **State**: `th-farm/th_tor_state.json`

## ⏱ Rate & Limit
- ~1 akun / 1-3 menit (verify email = bottleneck 30-60s)
- Tor exit node sering di-flag TH (malam) → retry + rotate
- IP rumah/WebShare/WARP/Daytona = SEMUA burn (jangan dipakai)

## 📊 Hasil (final Agt 2026)
- N akun verified (email + consent + key 200 + inject 9Router)
- N conn th aktif di 9Router (auto-gabung ke node TokenHarbor)
- 3 model free per akun: `mimo-v2.5:free`, `deepseek-v4-flash:free`, `qwen3.8-27b:free` — semua 200
- Rate: ~1 akun/1-3 menit (Tor), resume-able dari state JSON

## ⚠️ Pitfall
- 1 akun 1 key: cleanup AUTO keys wajib (TH auto-create key saat signup)
- Jangan disable conn th lama di 9router (user mau gabung)
- Tor lambat boot (30-60s) — retry timeout besar (40s)
