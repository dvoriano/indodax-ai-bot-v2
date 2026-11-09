# M03 PUBLIC REST HTTP 403 | audit sumber dan diagnosis lokal (9 Nov 2026)

**Bukti yang sudah ada di original VPS**: kolektor BTC PUBLIC WebSocket pernah menyelesaikan 10/10; `indodax-v2-m03-mainpath.service` mengembalikan exit 3 karena scanner LIGHT maupun DEEP20 gagal `PUBLIC_HTTP_OR_JSON_UNAVAILABLE`. Dua GET asli Indodax `/api/pairs` dan `/api/server_time` dari egress IPv4 `43.134.68.108`, melalui `IndodaxPublicMarket._get`, mendapat HTTP 403, `Server: cloudflare`, header `CF-Ray` ada, `Retry-After` tidak ada. Penolakan HTTP sudah melewati original local egress admission dan quota pada dua percobaan itu. **Aturan Cloudflare, penyebab 403, kemungkinan proxy, dan lokasi pengambil keputusan belum dibuktikan.** Jangan klaim permanent IP ban atau Indodax salah. Bot trading NO/NO/DRY_RUN.

## Audit repository, bukan klaim runtime

- `indodax_ai_bot/public_market.py`: API base `https://indodax.com`, `urllib.request.Request` dengan `Accept: application/json`, User-Agent `indodax-ai-bot-v2-public/0.2`, timeout 8s. `urlopen` bisa menggunakan proxy melalui environment. Error `OSError` (termasuk HTTPError 403) dan JSON `ValueError` digabung menjadi satu `PUBLIC_HTTP_OR_JSON_UNAVAILABLE`.
- `scanner.py`: `/api/pairs`, `/api/server_time`, `/api/ticker_all`, tidak memerlukan API key. URL dan endpoint dicocokkan dengan dokumentasi Public REST resmi `btcid/indodax-official-api-docs`.
- `m03_egress_admission.py` dan `public_rest_budget.py`: memeriksa owner standing approval/BTC freshness dan reserve kuota sebelum GET. `docs/M10_EGRESS_IP_ACCEPTANCE_PROOF.md` jelas menyatakan *seluruh* pemakai outbound egress/NAT dan peak 60 detik belum terverifikasi independen.
- `deploy/indodax-v2-m03-mainpath.service` memasang perintah original `--scheduled-public-scanner-once`. M05 Telegram dan BTC WS berbeda transport, sehatnya kedua service bukan bukti REST Indodax sehat.

## Periksa konfigurasi lokal dahulu, tanpa satu pun network GET

Di original VPS, setelah source merged+synced dan git clean, jalankan:

```bash
(
  set -euo pipefail
  cd "$HOME/indodax-ai-bot-v2"
  .venv/bin/python -B -m unittest -q tests.test_m03_http_access_diagnostic
  .venv/bin/python -B -m indodax_ai_bot.m03_http_access_diagnostic
)
```

Output **hanya nama proxy keys**, tidak pernah nilai, host, port, user/pass, token, API key, header, body, atau raw systemd. Data berasal dari: environment proses diagnostik SSH, `urllib.request.getproxies()` di proses tersebut, `systemctl --user show-environment` (nama-nama saja), dan metadata fixed `indodax-v2-m03-mainpath.service` `Environment/PassEnvironment/UnsetEnvironment/EnvironmentFiles/FragmentPath/ExecStart/ActiveState`. Tidak membaca EnvironmentFile, tidak mengakses unit bot lain dan tidak membaca /proc. Tidak mengubah unit, restart, HTTP, quota DB, trader, cloud.

**Batas**: shell SSH bukan bukti environment aktual M03 saat systemd mengeksekusi; systemd EnvironmentFile bisa menyembunyikan proxy (laporkan presence, tidak membuka). `unit_fragment_matches_original_v2` dan `unit_exec_uses_original_v2_checkout` hanya metadata, bukan attestation file byte/source/runtime. Daftar proxy kosong tidak membuktikan koneksi direct jika ada transparent egress proxy/cloud WAF. Keberadaan proxy key juga tidak membuktikan proxy tersebut dipakai.

Selanjutnya, jika tidak ada kesalahan lingkungan lokal yang terlihat, gunakan CF-Ray ID dan UTC timestamp untuk tiket resmi ke Indodax terkait alasan WAF/edge HTTP 403, bukan brute-force, rotasi IP, perubahan User-Agent secara spekulatif, ataupun host alternatif. Jangan kirim kredensial. M03 baru PASS setelah real public source normal dan original scanner menghasilkan data segar, bukan cached/replay. **Checklist master tetap terbuka** sampai bukti VPS disampaikan.

## Owner original VPS follow-up (9 Nov): `EnvironmentFiles` lokal ditemukan

Owner menyinkronkan V2 `main` sampai `11dedff` dan menjalankan 5/5 tes `m03_http_access_diagnostic` PASS. Output lokal: `process_proxy_keys=[]`, `manager_proxy_keys=[]`, `unit_explicit_proxy_keys=[]`, `unit_pass_proxy_keys=[]`, `unit_unset_proxy_keys=[]`, `urllib_proxy_schemes=[]`, tetapi **`unit_environment_file_present=true`**, `unit_fragment_matches_original_v2=true`, `unit_exec_uses_original_v2_checkout=true`, M03 `failed/exit-code/3`, M05 `active` dan `NRestarts=0` saat snapshot. **Unit `deploy/indodax-v2-m03-mainpath.service` di GitHub tidak memuat `EnvironmentFile`**, sehingga deklarasi yang terpasang kemungkinan berasal dari overlay/drop-in lokal; belum pasti. Tidak ada satu request API pada pemeriksaan.

Tool tambahan **`indodax_ai_bot.m03_envfile_proxy_keys_readonly`** secara sengaja terpisah dan hanya boleh memeriksa envfile **yang dideklarasikan oleh fixed ORIGINAL M03 unit**, setelah memastikan metadata FragmentPath dan original Python checkout cocok. Hanya membuka file reguler owner-private tanpa symlink di `~/.config/indodax-ai-bot-v2`, ukuran maksimal 64 KiB, menampilkan hitungan file, key PROXY yang masuk allowlist, dan status seluruh file diperiksa. Semua path, URL, password, secret, nama file dan nilai env tetap tersembunyi. File tidak memenuhi syarat atau parsing metadata ambigu dilaporkan `skipped` / `all_checked=false`, **bukan** "tidak ada proxy". Tanpa HTTP, DNS, unit restart, perubahan izin/file, secret dump, atau trade.

Setelah PR merged, jalankan satu kali di original VPS (clean FF source):

```bash
.venv/bin/python -B -m unittest -q tests.test_m03_envfile_proxy_keys_readonly
.venv/bin/python -B -m indodax_ai_bot.m03_envfile_proxy_keys_readonly
```

Jika envfile tidak mengandung proxy dan `all_checked=true`, tidak ada bukti **proxy via envfile** M03; Cloudflare edge restriction masih memerlukan provider/WAF evidence dan/atau audit egress eksternal. Jika key ditemukan, periksa konfigurasi instalasi secara owner-only tanpa mencetak nilainya, jangan langsung mengubahnya. `M03` tetap exit 3; `M05` stabil hanya pada snapshot, bukan SLA 24 jam. Tidak ada checkbox penuntasan baru.
