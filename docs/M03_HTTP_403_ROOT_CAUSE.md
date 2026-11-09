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

## Bukti lanjutan: 9 November 2026
Original VPS: satu file envfile M03 berhasil diperiksa (inspected=1, skipped=0, all_checked=true), environment_proxy_keys kosong. Timer M03 benar-benar aktif, last trigger 12:58:10 CST, next trigger 12:59:10 CST, tetapi service tetap exit 3. BTC M10 snapshot terbaru batch 10/10, age 28 detik, no alert, BTC timer aktif dan service oneshot sukses. Screenshot Telegram BTC 3/10 pagi adalah pesan historical, bukan batch terkini; M10 24h acceptance tetap OPEN.

### Satu public-only Cloudflare edge evidence
Perintah `python -B -m indodax_ai_bot.m03_cloudflare_edge_evidence_once` dijalankan secara manual oleh owner setelah source dan tes tersinkron. Memanggil original `IndodaxPublicMarket._get('/api/server_time')` hanya sekali, dengan original M03 egress admission dan shared SQLite budget, NO/NO/DRY_RUN. Adapter urllib sendiri mungkin mengikuti redirect, jadi satu adapter invocation tidak selalu hanya satu HTTP hop. Tidak mengubah User-Agent, proxy, header, IP, unit, timer ataupun data pasar. Hasil hanya UTC timestamp, status HTTP, CF-Ray format terbatas, challenge bool cf-mitigated, Cloudflare server bool, retry-after presence, content-type kategori, dan count adapter. Body, cookie, error exception, secret, raw header tidak diekspor. Tidak ada network retry. Jika Cloudflare 403 masih ada, gunakan Ray+timestamp untuk pemeriksaan pemilik WAF/support Indodax. Jangan menyamakan Ray dengan bukti root cause atau blacklist IP. Jika HTTP pulih, M03 baru dapat dianggap sehat setelah scanner original menghasilkan data baru, bukan cuma satu endpoint OK.

## 9 Nov 2026 | Perbandingan kemarin berhasil versus sekarang HTTP403

**Penilaian akar masalah masih OPEN.** BUKTI bahwa scanner sempat sehat bukan bukti bahwa sistem eksternal/Indodax kemudian salah: perlu memisahkan kode, local admission, installed runtime, dan Cloudflare response.

### Kronologi bukti pada repo asli
- **6 Nov 21:00 sampai 7 Nov 06:59 WIB:** `docs/ROADMAP.md` mencatat original M03 `public_ticker_minute_samples` 10 hourly windows dengan jumlah minute 60,60,60,59,60,60,58,55,58,60, sekitar 18k–20k samples/jam dan last age 24 detik. Real cached historical ticker snapshots, bukan bukti semua transaksi/tape lengkap.
- **8 Nov:** snapshot original M03 timer active/waiting tetapi service failed; last 10m M03 0 pairs, source age None. Pada snapshot yang sama BTC WS service Result=exec-condition dan known standing BTC gate memerlukan fresh clean batch. Status M03 yang gagal saat itu **belum mengidentifikasi HTTP 403 sebagai sebab pertama**. Jangan memundurkan penanggalan Cloudflare 403 ke 8 Nov tanpa bukti journald.
- **9 Nov:** BTC WS berhasil pulih 10/10, age28s dan normal scheduled timer; M03 timer terbukti memicu setiap menit, service exit3. Original source `_get('/api/pairs')` dan `_get('/api/server_time')` secara terpisah menerima HTTP403 dari Cloudflare; satu controlled edge probe /api/server_time pada 2026-11-09 05:11:06 UTC memberi Ray `a57a5098cc3e938e-SIN`, `cf_challenge=false`, HTML 403, no Retry-After, IPv4 VPS outbound terobservasi 43.134.68.108. Proxy keys di process/user systemd/unit/1 owner-private envfile semuanya kosong; envfile 1/1 inspected. Ini membuktikan real HTTP denial sekarang, **tidak** membuktikan WAF rule/penyebab/siapa yang mengubahnya.

### Perbandingan perubahan GitHub original
- File `indodax_ai_bot/public_market.py` HTTP transport `_http_json`: dibandingkan pada commit original `99249afb23687329b65dfc70083952f2f6275cb4` (26 Oct), `ee123151941e3e65abb36fe76daf3aa5e172b535` (3 Nov) dan `main` 9 Nov. Base `https://indodax.com`, Request headers `Accept: application/json` dan `User-Agent: indodax-ai-bot-v2-public/0.2`, `urlopen(timeout=8)`, JSON parser dan wrapped error **sama**. Tambahan Nov3 hanya guard per GET (egress admission/shared budget), yang dua request 9 Nov diketahui berhasil dilewati karena sampai HTTP403.
- Latest source commit paths menurut GitHub history: `public_market.py` 3 Nov, `scanner.py` 2 Nov, `public_rest_budget.py` 3 Nov, `m03_egress_admission.py` 6 Nov, shipped `deploy/indodax-v2-m03-mainpath.service` 4 Nov, timer file 29 Oct, `__main__.py` 6 Nov. Semua ini sebelum real successful original M03 nightly window 6–7 Nov. **Tidak ditemukan patch HTTP transport pada 8–9 Nov yang menjelaskan peralihan ke HTTP403.** Install state dan owner-only systemd drop-in masih bisa berbeda dari shipped source.

### Cocokkan dengan ketentuan resmi Indodax
- Official repository https://github.com/btcid/indodax-official-api-docs/blob/master/Public-RestAPI.md tetap mendefinisikan base `https://indodax.com`, GET `/api/server_time`, `/api/pairs`, `/api/ticker_all`, tanpa API key dan rate 180 requests per minute. Histori commit file tersebut menunjukkan update terakhir pada **10 September 2026**, bukan 8–9 November. Artinya **tidak ada perubahan publik pada file spec yang ditemukan**, BUKAN bukti internal Cloudflare/WAF/security/rate policy tidak berubah.
- Official Indodax help article tentang IP Permission untuk **private Trade API keys** menyebut IP Permission diperlukan saat izin withdrawal; ini **bukan** alasan mengubah View Only API key demi menyelesaikan HTTP403 pada PUBLIC GET yang tidak memiliki auth.
- Official Cloudflare 403 docs menyebut WAF managed/custom rules, security settings, origin access rules, SNI/early edge validation sebagai kemungkinan. `Server: cloudflare`, CF-Ray, `cf_mitigated=false` saja **tidak** menunjukkan rule WAF tepat atau membuktikan IP permanen di-block. Cloudflare Ray + UTC timestamp harus dicek oleh operator zone.

### Sisa hipotesis yang belum diverifikasi
1. WAF/IP/ASN or client/request classification by Indodax Cloudflare (may change dynamically without docs change).
2. External consumers/NAT sharing outbound IPv4 with V2 and actual rolling 60s public REST demand (explicit OPEN in `M10_EGRESS_IP_ACCEPTANCE_PROOF.md`, local cap not complete IP-wide meter).
3. Network route/transparent egress proxy/host DNS/TLS/Cloudflare path not visible via checked process environment; however HTTP403 proves HTTPS request reached an HTTP-speaking layer.
4. Original installed unit's runtime overrides/drop-ins beyond proxy keys, with any behavior difference from shipped service. Snapshot checks original fragment/exec and EnvFile but not full running service environment or all non-proxy metadata.
5. Time when HTTP403 *first began* vs BTC HOLD/runtime failure on 8 Nov remains unknown; historical M03 journal/last SQLite ticker minute can identify exact last successful sample and first `PUBLIC_HTTP_OR_JSON_UNAVAILABLE` wrapper, not 403 code until edge probe 9 Nov.

**Operator next action:** One no-network original SQLite + sanitized M03 journal retrospective to time the last known success and first upstream-failure reason, without printing raw logs or wallet/order data. Keep normal timer 24/7 as owner directed, BTC and Telegram unchanged, trading NO/NO/DRY_RUN. Do not repeatedly probe/rotate IP/UA or claim origin of 403 until upstream Cloudflare security logs via Ray ID or independent gateway evidence.

### 9 Nov: pembanding independen di luar VPS
- Akses web terpisah dari VPS melalui penelusuran publik ke `https://indodax.com/api/server_time` berhasil mendapat JSON `{"timezone":"UTC","server_time":1794198864862}`, yang menyatakan epoch **2026-11-09 04:34:24.862 UTC**. Ini sumber dari sisi lingkungan web berbeda, **bukan tes jaringan dari VPS**. Endpoint tersebut tidak bisa disebut mati secara global pada saat itu.
- Owner original VPS `IndodaxPublicMarket._get('/api/server_time')` dengan explicit bot UA tetap HTTP403 Cloudflare pada **2026-11-09 05:11:06 UTC**. Dua observasi terpisah waktu ~37 menit dan koneksi: perbedaan akses bisa dipengaruhi IP/ASN/geografi/Cloudflare WAF/client UA/TLS fingerprint/intermediary, sehingga tidak membuktikan akar masalah, hanya memperkuat bahwa outage tidak universal.
- Tautan langsung official endpoint publik: https://indodax.com/api/server_time. Jangan menyimpulkan keberhasilan browser/web tool sama dengan izin untuk melakukan spoofing User-Agent atau rotasi IP di VPS.
