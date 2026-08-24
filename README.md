# TokenHarbor Farmer — Auto Register + Free Models + 9Router Inject

Auto-register akun [TokenHarbor](https://tokenharbor.ai), enable free models, buat API key, dan inject ke 9Router. **Bypass IP rate-limit via TOR exit node rotation** (gratis, unlimited IP).

## ✨ Fitur
- ✅ Auto-register via **Next.js Server Action** (tembus `signup_ip_required`)
- ✅ **TOR rotation** (NEWNYM) — bypass "Too many sign-ups from this network"
- ✅ 1 akun = 1 API key (auto-cleanup key bawaan)
- ✅ Enable free models otomatis (`free_models_enabled: true`)
- ✅ Verify email (temp mail poll + auto-click link)
- ✅ Test key (`mimo-v2.5:free` / `deepseek-v4-flash:free`)
- ✅ Inject ke 9Router (GABUNG — tidak hapus key lama)
- ✅ Resume dari crash (state JSON)
- ✅ Loop register→logout

## 📋 Persyaratan
1. **Python 3.10+** + `pip install requests pysocks`
2. **Tor** (download [tor-expert-bundle](https://www.torproject.org/download/tor/)):
   ```
   # torrc
   SocksPort 9050
   ControlPort 9051
   CookieAuthentication 0
   DataDirectory C:/path/to/tor/data
   ```
   Jalankan: `tor.exe -f torrc`
3. **9Router** (opsional — kalau inject): path DB via env `NINE_ROUTER_DB`

## 🚀 Cara Pakai

### 1. Register 1 akun (cek alur)
```bash
python th_auto_register.py single
```

### 2. Farm massal (resume-able)
```bash
python th_tor_farm.py 100            # 100 akun, inject 9router
python th_tor_farm.py 50 --no-inject # tanpa inject
```
Auto-save ke `th_tor_state.json` — restart = resume.

### 3. Loop register→logout
```bash
python th_auto_register.py loop 10
python th_auto_register.py loop 10 --no-inject
```

### 4. Test semua key
```bash
python th_auto_register.py test      # (tambah mode test di th_auto_register)
```

## ⚙️ Konfigurasi (env)
```bash
export NINE_ROUTER_DB="C:/path/9router/data.sqlite"   # DB 9router
export TH_ANON_KEY="..."                              # Supabase anon (untuk logout)
```

## 📊 Hasil Verified (Agt 2026)
- **75+ akun** berhasil (semua verified + consent + test 200)
- Rate: ~1 akun/1-3 menit (tergantung Tor)
- 100% akun aktif & bisa dipakai via 9Router (`th/deepseek-v4-flash:free` = 200)

## 📖 Detail Teknis
Baca `STEPBYSTEP.md` — full workflow: Next.js Server Action multipart body, action IDs, endpoint, error handling, rate-limit bypass.

## ⚠️ Catatan
- **Email**: pakai tempmail.lol (domain acak: totalgamehub.net, birdzpt.com, foodtrik.com)
- **Tor exit node kadang di-flag** TH → script auto-rotate (NEWNYM) + retry
- **1 akun = 7 hari free** setelah enable (per akun, bukan unlimited) — farming massal = stock berlapis
- Jangan pakai IP rumah/WebShare/WARP untuk TH — sudah di-flag
- Gunakan untuk akun & workflow milik sendiri; tanggung jawab penggunaan = pengguna

## 🧪 Test
```bash
python -c "import th_tor_farm"   # import OK
python th_auto_register.py single  # e2e 1 akun
```