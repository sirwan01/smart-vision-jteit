# Panduan Pemasangan di Server Kampus

Smart Vision JTEIT — Presensi Otomatis Berbasis Wajah
Jurusan Teknik Elektronika dan Teknologi Informasi, Fakultas Teknik, Universitas Negeri Makassar

Panduan ini ditujukan bagi admin server/UPT TIK atau teknisi jurusan. Hasil akhirnya:
aplikasi berjalan sebagai layanan `systemd`, diakses lewat **HTTPS** melalui Nginx,
dengan **cadangan basis data harian** otomatis.

```
Peramban dosen / tablet kios ─┐
                              ├─HTTPS─► Nginx (443) ──► uvicorn 127.0.0.1:8000 ──► SQLite
PC kamera kelas (X-API-Key) ──┘          sertifikat        1 worker, systemd       /var/lib/...
                                                                     │
                                         timer harian ──► scripts/cadangkan.py ──► /var/backups/...
```

---

## 1. Kebutuhan

| Komponen | Minimum | Catatan |
|---|---|---|
| Sistem operasi | Ubuntu Server 22.04 / 24.04 LTS | Berkas konfigurasi diuji dengan Nginx 1.24 (Ubuntu 24.04) |
| CPU / RAM | 2 vCPU / 4 GB | Pengenalan ±50 ms per bingkai pada 4 inti; tanpa GPU |
| Disk | 10 GB | Aplikasi + lingkungan Python ±300 MB, model ±40 MB, sisanya data & cadangan |
| Python | 3.10 atau lebih baru | Diuji pada 3.13; jalankan `pytest` setelah instalasi (§3) |
| Jaringan | Nama domain + sertifikat TLS | Mis. subdomain dari UPT TIK; kamera di peramban **wajib HTTPS** |
| Akses keluar (sekali) | PyPI, `github.com`, `media.githubusercontent.com` | Untuk paket Python dan unduhan model |

## 2. Menyiapkan server

```bash
sudo apt update
sudo apt install -y python3 python3-venv nginx sqlite3 git
sudo timedatectl set-timezone Asia/Makassar
```

Buat akun sistem khusus (tanpa login) dan folder kerja:

```bash
sudo useradd --system --no-create-home --home-dir /opt/smart-vision-jteit --shell /usr/sbin/nologin presensi
sudo install -d -o presensi -g presensi -m 755 /opt/smart-vision-jteit
sudo install -d -o presensi -g presensi -m 750 /var/lib/smart-vision-jteit
sudo install -d -o presensi -g presensi -m 700 /var/backups/smart-vision-jteit
sudo install -d -o root -g presensi -m 750 /etc/smart-vision-jteit
```

## 3. Memasang aplikasi

```bash
sudo -u presensi git clone https://github.com/sirwan01/smart-vision-jteit.git /opt/smart-vision-jteit
```

> Bila repositori privat, gunakan *deploy key* (SSH, hanya-baca) dari pengaturan repositori
> GitHub, bukan kata sandi atau token pribadi.

Lalu pasang dependensi dan model:

```bash
cd /opt/smart-vision-jteit
sudo -u presensi python3 -m venv .venv
sudo -u presensi .venv/bin/pip install --upgrade pip
sudo -u presensi .venv/bin/pip install -r requirements.txt
sudo -u presensi .venv/bin/python scripts/unduh_model.py
sudo -u presensi .venv/bin/python -m pytest -q
```

`unduh_model.py` memverifikasi *checksum* SHA-256 keempat model. `pytest` harus berakhir
dengan `passed`; uji yang memerlukan foto contoh akan dilewati (*skipped*), itu normal.

## 4. Konfigurasi

```bash
sudo cp deploy/env.contoh /etc/smart-vision-jteit/env
sudo chown root:presensi /etc/smart-vision-jteit/env
sudo chmod 640 /etc/smart-vision-jteit/env
openssl rand -hex 32          # salin hasilnya sebagai API_KEY
sudo nano /etc/smart-vision-jteit/env
```

| Variabel | Nilai produksi | Fungsi |
|---|---|---|
| `DB_PATH` | `/var/lib/smart-vision-jteit/presensi.db` | Basis data |
| `MODEL_DIR` | `/opt/smart-vision-jteit/models` | Model ONNX |
| `TZ` | `Asia/Makassar` | Waktu presensi dalam WITA |
| `API_KEY` | hasil `openssl rand -hex 32` | Kunci perangkat kamera kelas (hanya dapat mengirim bingkai) |
| `WAJIB_HIDUP_AKTIF_DAFTAR` | `1` | Pendaftaran wajah wajib verifikasi gerak kepala |
| `COOKIE_AMAN` | `1` | Cookie login hanya terkirim lewat HTTPS |
| `AMBANG_KEMIRIPAN`, `AMBANG_HIDUP` | `0.363`, `0.5` | Kalibrasi setelah uji coba kelas |

## 5. Akun admin pertama

```bash
cd /opt/smart-vision-jteit
sudo -u presensi bash -c 'set -a; . /etc/smart-vision-jteit/env; set +a; .venv/bin/python scripts/kelola_pengguna.py tambah admin --nama "Admin JTEIT" --peran admin'
```

Kata sandi diminta dua kali tanpa tampil di layar (minimal 8 karakter). Akun dosen
selanjutnya dibuat dari dasbor, menu **Pengguna**.

## 6. Layanan systemd

```bash
sudo cp deploy/smart-vision-jteit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-vision-jteit
systemctl status smart-vision-jteit
curl -s http://127.0.0.1:8000/kesehatan
```

Hasil `curl` yang benar: `{"status":"ok","pendaftaran_wajib_aktif":true}`.

Catatan penting berkas layanan:

- **`--workers 1` wajib.** Tantangan verifikasi gerak disimpan di memori proses dan SQLite
  paling aman dengan satu penulis. Satu worker cukup untuk skala jurusan.
- Aplikasi hanya mendengarkan `127.0.0.1`; tidak dapat diakses langsung dari jaringan.
- `--proxy-headers --forwarded-allow-ips 127.0.0.1` membuat aplikasi mengenali HTTPS
  dari Nginx (dibutuhkan untuk cookie `Secure`).
- Pengerasan: `ProtectSystem=strict`, hanya `/var/lib/smart-vision-jteit` yang dapat ditulis.

## 7. Nginx dan HTTPS

```bash
sudo cp deploy/nginx-smart-vision-jteit.conf /etc/nginx/sites-available/smart-vision-jteit
sudo nano /etc/nginx/sites-available/smart-vision-jteit   # ganti presensi.contoh.ac.id (3 tempat)
```

**Sertifikat** — salah satu:

- dari UPT TIK: letakkan di `/etc/ssl/smart-vision-jteit/fullchain.pem` dan `privkey.pem`
  (`chmod 600` untuk kunci privat); atau
- Let's Encrypt (bila server dapat diakses publik pada port 80):
  `sudo apt install -y certbot python3-certbot-nginx` lalu
  `sudo certbot --nginx -d <domain>`, dan sesuaikan baris `ssl_certificate*`.

```bash
sudo ln -s /etc/nginx/sites-available/smart-vision-jteit /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

> **Server tanpa IPv6:** bila `nginx -t` menampilkan
> `socket() [::]:80 failed (97: Address family not supported by protocol)`,
> hapus dua baris `listen [::]:...` dari berkas situs.

Konfigurasi ini sudah memuat: pengalihan HTTP→HTTPS, HTTP/2, HSTS, `X-Frame-Options: DENY`,
`nosniff`, izin kamera hanya untuk situs sendiri, dan batas unggahan 2 MB.

## 8. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Port 8000 **tidak** dibuka. Bila server hanya untuk jaringan kampus, batasi akses 443 ke
rentang IP kampus sesuai arahan UPT TIK.

## 9. Cadangan basis data

```bash
sudo cp deploy/smart-vision-jteit-cadangan.service deploy/smart-vision-jteit-cadangan.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-vision-jteit-cadangan.timer
sudo systemctl start smart-vision-jteit-cadangan.service     # uji sekarang
sudo ls -l /var/backups/smart-vision-jteit
systemctl list-timers smart-vision-jteit-cadangan.timer
```

Setiap hari pukul ±01.17 WITA skrip membuat salinan **konsisten** (API backup SQLite,
aman walau aplikasi berjalan), memeriksa integritasnya, mengompresi (`.db.gz`, izin 600),
dan menghapus cadangan yang lebih tua dari 30 hari.

**Salinan di luar server (wajib).** Cadangan di disk yang sama ikut hilang bila disk
rusak. Salin berkala ke server/NAS lain milik jurusan, misalnya:

```bash
rsync -a --delete /var/backups/smart-vision-jteit/ cadangan@nas-jurusan:/cadangan/presensi/
```

Karena berisi data biometrik, enkripsi salinan di luar server (mis. `gpg --symmetric`)
bila media penyimpanannya tidak sepenuhnya dikendalikan jurusan.

**Pemulihan:**

```bash
sudo systemctl stop smart-vision-jteit
cd /opt/smart-vision-jteit
sudo -u presensi bash -c 'set -a; . /etc/smart-vision-jteit/env; set +a; .venv/bin/python scripts/cadangkan.py --pulihkan /var/backups/smart-vision-jteit/presensi-YYYYMMDD-HHMMSS.db.gz'
sudo systemctl start smart-vision-jteit
```

Berkas cadangan diperiksa dulu; basis data yang sedang ada disimpan sebagai
`presensi.db.sebelum-pulih-<waktu>`, dan berkas rusak ditolak tanpa mengubah apa pun.
**Uji pemulihan ke server/folder lain minimal sekali per semester.**

## 10. Kamera kelas

| Pilihan | Cocok untuk | Cara |
|---|---|---|
| Laptop dosen sebagai kamera | Kelas kecil–sedang | Dasbor → detail sesi → *Jadikan peramban ini kamera kelas* |
| Mode kios (verifikasi gerak) | Pencegahan titip absen, ujian | Tablet/laptop di pintu kelas → *Mode kios* |
| PC kamera tetap di kelas | Ruang dengan kamera terpasang | `scripts/kamera_presensi.py` dengan `API_KEY` |

Contoh PC kamera (kamera USB indeks 0 atau URL RTSP kamera IP):

```bash
API_KEY=<kunci dari /etc/smart-vision-jteit/env> \
python scripts/kamera_presensi.py --api https://<domain> --sesi <ID sesi> --kamera 0 --interval 3
```

Simpan `API_KEY` di PC kamera dengan izin berkas terbatas; ganti kunci bila PC hilang
atau pindah tangan (ubah di `/etc/smart-vision-jteit/env`, lalu `systemctl restart`).
Sesi yang dicentang *wajib verifikasi gerak* menolak kiriman kamera pasif.

## 11. Pembaruan aplikasi

```bash
sudo systemctl start smart-vision-jteit-cadangan.service      # cadangkan dulu
cd /opt/smart-vision-jteit
sudo -u presensi git pull
sudo -u presensi .venv/bin/pip install -r requirements.txt
sudo -u presensi .venv/bin/python scripts/unduh_model.py
sudo -u presensi .venv/bin/python -m pytest -q
sudo systemctl restart smart-vision-jteit
curl -s https://<domain>/kesehatan
```

Perubahan struktur basis data (*migrasi*) dijalankan otomatis saat aplikasi mulai.

## 12. Pemantauan

```bash
journalctl -u smart-vision-jteit -f                  # log aplikasi
journalctl -u smart-vision-jteit-cadangan --since today
sudo tail -f /var/log/nginx/error.log
```

Pantau `https://<domain>/kesehatan` dari sistem pemantauan kampus (respons 200 = sehat).

## 13. Daftar periksa keamanan & pelindungan data pribadi

- [ ] Repositori GitHub dijadikan **privat**; folder `data/` dan berkas `.db` tidak pernah di-commit
- [ ] HTTPS aktif dengan sertifikat sah; `COOKIE_AMAN=1`
- [ ] `API_KEY` acak 64 karakter heksadesimal; hanya diketahui admin dan PC kamera
- [ ] `WAJIB_HIDUP_AKTIF_DAFTAR=1`
- [ ] `/etc/smart-vision-jteit/env` berizin 640 (root:presensi)
- [ ] Hanya port 22/80/443 terbuka; akses SSH dengan kunci, bukan kata sandi
- [ ] Cadangan harian berjalan, salinan di luar server terenkripsi, pemulihan pernah diuji
- [ ] Akun dosen yang tidak aktif (pindah/pensiun) dinonaktifkan
- [ ] Formulir persetujuan biometrik mahasiswa diarsipkan jurusan
- [ ] Kebijakan retensi ditetapkan (mis. data wajah dihapus setelah mahasiswa lulus)
      dan dijalankan admin lewat menu *Mahasiswa* → *Hapus* / *Cabut persetujuan*
- [ ] Penilaian dampak pelindungan data pribadi (UU No. 27/2022) dikoordinasikan dengan
      pengelola data universitas sebelum penggunaan resmi

## 14. Pemecahan masalah

| Gejala | Penyebab umum | Tindakan |
|---|---|---|
| `nginx -t` gagal: `unknown directive "http2"` | Berkas situs memakai sintaks Nginx ≥ 1.25.1 | Gunakan `listen 443 ssl http2;` seperti di berkas `deploy/` |
| `nginx -t` gagal: `[::]:80 ... Address family not supported` | Server tanpa IPv6 | Hapus baris `listen [::]:...` |
| 502 Bad Gateway | Layanan aplikasi mati | `systemctl status smart-vision-jteit`, `journalctl -u smart-vision-jteit -n 50` |
| Selalu kembali ke layar masuk | Diakses lewat HTTP sementara `COOKIE_AMAN=1` | Akses lewat `https://` |
| Kamera tidak menyala di peramban | Halaman tidak dibuka lewat HTTPS, atau izin kamera ditolak | Gunakan HTTPS; izinkan kamera di pengaturan situs peramban |
| 413 Request Entity Too Large | Gambar/CSV > 2 MB | Kecilkan resolusi kamera atau pecah berkas CSV |
| `Model ... belum ada` di log | Model belum diunduh / `MODEL_DIR` salah | `scripts/unduh_model.py`, periksa `MODEL_DIR` |
| Jam presensi tidak sesuai WITA | `TZ` belum diisi | `TZ=Asia/Makassar` di berkas env, lalu restart |
| Verifikasi gerak sering gagal | Pencahayaan dari belakang, wajah terlalu jauh/kecil | Lampu dari depan, jarak 50–100 cm, kamera setinggi wajah |
| Mahasiswa sering tidak dikenali | Foto pendaftaran sedikit/kurang variatif | Daftarkan 3–5 foto per mahasiswa dalam kondisi kelas |

## 15. Yang telah divalidasi

Pada Ubuntu 24.04 (kontainer uji, Python 3.13, Nginx 1.24):

- `systemd-analyze verify` untuk ketiga berkas unit;
- `nginx -t` dan uji jalan Nginx (sertifikat uji) → uvicorn dengan argumen persis seperti
  berkas layanan: pengalihan 301 ke HTTPS, HTTP/2, kelima header keamanan, cookie
  `Secure; HttpOnly; SameSite=strict`, unggahan 3 MB ditolak (413), kamera kelas dengan
  `API_KEY` diterima dan tanpa kunci ditolak (401);
- cadangan saat aplikasi berjalan, retensi 30 hari, pemulihan ke server baru, dan
  penolakan berkas cadangan rusak (juga tercakup `tests/test_cadangan.py`).

Belum divalidasi di kontainer: `systemctl enable/start` sungguhan, `ufw`, `certbot`, dan
akses dari jaringan kampus. Lakukan langkah §6–§9 di server dan periksa keluarannya.
