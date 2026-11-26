# Smart Vision JTEIT — Presensi Otomatis Berbasis Wajah

Sistem presensi mahasiswa berbasis pengenalan wajah untuk Jurusan Teknik Elektronika
dan Teknologi Informasi (JTEIT), Fakultas Teknik, Universitas Negeri Makassar.

Kamera kelas mengirim bingkai gambar ke API. Sistem mendeteksi semua wajah,
mencocokkannya dengan wajah mahasiswa yang telah terdaftar, lalu mencatat kehadiran
pada sesi perkuliahan yang sedang dibuka.

## Arsitektur

```
Kamera kelas ──(JPEG tiap n detik)──► API FastAPI ──► YuNet (deteksi wajah)
 scripts/kamera_presensi.py                       └─► SFace (embedding 128-D)
                                                  └─► MiniFASNet (wajah hidup / anti-spoofing)
                                                  └─► Pencocokan kosinus ≥ 0,363
                                                  └─► SQLite: mahasiswa, wajah, sesi, presensi
```

| Komponen | Pilihan | Alasan |
|---|---|---|
| Deteksi wajah | YuNet (OpenCV Zoo, 230 KB) | Cepat di CPU, tahan variasi pose |
| Pengenalan wajah | SFace (OpenCV Zoo, 37 MB) | Akurasi LFW 99,4%, tanpa GPU/PyTorch |
| Anti-spoofing | MiniFASNetV2 + V1SE (Silent-Face, 2 × 1,7 MB) | Menolak foto cetak/layar di depan kamera |
| API | FastAPI | Dokumentasi otomatis di `/docs` |
| Basis data | SQLite | Cukup untuk skala jurusan; mudah dipindah ke PostgreSQL |

Pada CPU 4 inti, satu bingkai berisi beberapa wajah diproses sekitar 50 ms.

## Menjalankan

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/unduh_model.py          # unduh + verifikasi checksum model
python scripts/kelola_pengguna.py tambah admin --nama "Admin JTEIT" --peran admin
uvicorn app.main:app --reload          # buka http://localhost:8000/dasbor
```

Akun admin pertama dibuat lewat baris perintah (kata sandi diminta tanpa tampil di
layar). Setelah itu admin membuat akun dosen dari menu **Pengguna** di dasbor.

Klien kamera (pada komputer yang terhubung ke webcam atau kamera IP):

```bash
API_KEY=kunci-perangkat python scripts/kamera_presensi.py --sesi 1 --api http://SERVER:8000 --interval 3
```

## Akun dan hak akses

| Peran | Hak |
|---|---|
| **Admin jurusan** | Semua sesi dan rekap, kelola akun (buat, nonaktifkan, reset sandi), hapus data mahasiswa |
| **Dosen** | Hanya sesi yang ia buka sendiri dan rekap mata kuliahnya; daftarkan mahasiswa dan foto wajah |
| **Perangkat kamera** (`X-API-Key`) | Hanya mengirim bingkai ke `POST /sesi/{id}/presensi`; tidak dapat membaca data |

- Kata sandi di-*hash* dengan scrypt + garam acak; minimal 8 karakter.
- Login menghasilkan cookie `HttpOnly`, `SameSite=Strict`, berlaku 12 jam; di server
  hanya disimpan SHA-256 tokennya. Atribut `Secure` aktif otomatis di balik HTTPS.
- 5 kali gagal masuk dalam 15 menit mengunci nama pengguna itu selama 15 menit.
- Ganti/reset sandi, penonaktifan, atau perubahan peran langsung mengakhiri sesi
  login lain milik akun tersebut.
- Sesi milik dosen lain dijawab 404 sehingga keberadaannya tidak bocor.
- Kelas paralel (kode MK sama, dosen berbeda) direkap terpisah per dosen; admin
  melihat gabungannya.

Pengelolaan dari baris perintah: `python scripts/kelola_pengguna.py daftar | tambah |
reset-sandi | nonaktifkan | aktifkan` (lihat `--help`).

## Dasbor web dosen

Buka `http://SERVER:8000/dasbor` (atau `/`) dan masuk dengan akun dosen/admin.

| Menu | Fungsi |
|---|---|
| **Sesi** | Ringkasan hari ini, buka/tutup sesi, daftar hadir langsung (segar tiap 5 detik), percobaan palsu, unduh CSV |
| **Kamera kelas dari peramban** | Laptop dosen menjadi kamera presensi: bingkai webcam dikirim tiap 3 detik |
| **Kelas** | Buat kelas (MK + kelas + semester), impor peserta dari CSV KRS, catat persetujuan, keluarkan peserta |
| **Mahasiswa** | Daftarkan mahasiswa, catat/cabut persetujuan, ambil foto wajah lewat webcam atau unggah berkas, cari, hapus data (admin) |
| **Rekap** | Per kelas: seluruh peserta KRS termasuk yang belum pernah hadir, plus yang hadir di luar KRS; per MK untuk sesi tanpa kelas. Batas minimal dapat diatur (bawaan 75%) |
| **Pengguna** (admin) | Buat akun dosen, nonaktifkan/aktifkan, reset kata sandi |
| **Ganti sandi** | Setiap pengguna dapat mengganti kata sandinya sendiri |

Webcam di peramban hanya berfungsi melalui **HTTPS** atau `localhost`.

## Kelas dan impor KRS

1. Menu **Kelas** → buat kelas, misalnya `TIK301` kelas `A`, semester `2026/2027 Ganjil`
   (admin dapat menetapkan dosen pengampu lain).
2. **Impor CSV KRS** dengan kolom `nim` (wajib), `nama`, `prodi`. Pemisah koma atau titik
   koma (format Excel Indonesia), UTF-8 atau Latin-1; tombol *Unduh templat CSV* tersedia.
   Baris yang ditolak (NIM tidak valid, mahasiswa baru tanpa nama) dilaporkan per nomor baris;
   impor ulang aman (tidak menggandakan peserta).
3. Saat membuka sesi, pilih kelasnya. Detail sesi menampilkan daftar **Belum hadir**
   secara langsung, termasuk peserta yang belum punya data wajah.
4. Menu **Rekap** → pilih kelas untuk melihat persentase seluruh peserta KRS.

**Persetujuan biometrik.** Mahasiswa yang masuk lewat impor KRS hanya tercatat
identitasnya. Foto wajah baru dapat diambil setelah dosen menekan **Catat persetujuan**
(setelah mahasiswa menyetujui). **Cabut persetujuan** menghapus seluruh data wajahnya,
sedangkan identitas dan riwayat presensi tetap.

## Alur penggunaan (API)

1. **Daftarkan mahasiswa** — `POST /mahasiswa` dengan `persetujuan: true`.
2. **Daftarkan wajah** — `POST /mahasiswa/{nim}/wajah` (foto berisi tepat satu wajah).
   Disarankan 3–5 foto per mahasiswa dengan pencahayaan dan sudut berbeda.
3. **Buka sesi** — `POST /sesi` (kode MK, nama MK, ruang, dosen).
4. **Presensi** — kamera mengirim bingkai ke `POST /sesi/{id}/presensi`; setiap
   mahasiswa dicatat sekali per sesi. Wajah yang terdeteksi sebagai foto/layar
   tidak dicatat dan masuk `ditolak_palsu`.
5. **Tutup sesi** — `POST /sesi/{id}/tutup`.
6. **Rekap** — `GET /sesi/{id}/presensi` (JSON) atau `GET /sesi/{id}/presensi.csv`;
   per mata kuliah: `GET /rekap` dan `GET /rekap/{kode_mk}?batas_persen=75`.
7. **Audit kecurangan** — `GET /sesi/{id}/percobaan-palsu` menampilkan waktu, skor,
   dan NIM yang diduga dipalsukan.

## Deteksi wajah hidup (anti-spoofing)

Setiap wajah dinilai oleh dua model MiniFASNet (skala potong 2,7 dan 4,0); rata-rata
probabilitas "asli" dibandingkan dengan `AMBANG_HIDUP` (bawaan 0,5).

| Kasus | Perlakuan |
|---|---|
| Foto pendaftaran tampak palsu | Ditolak (HTTP 422) |
| Wajah palsu di kamera kelas | Tidak dicatat hadir; dicatat di `percobaan_palsu` |
| Wajah asli | Dicatat hadir beserta `skor_hidup` |

Pada contoh uji, wajah asli mendapat skor 1,000, sedangkan foto cetak/layar 0,014–0,075.

**Keterbatasan yang perlu diketahui:**
- Model mendeteksi wajah yang *difoto ulang* (cetakan atau layar di depan kamera).
  Ia **tidak** dapat menolak berkas foto digital yang dikirim langsung ke API.
  Karena itu, isi `API_KEY` dan pastikan hanya kamera kelas yang memegang kuncinya.
- Wajah yang sangat kecil (jauh dari kamera) dan pencahayaan ekstrem menurunkan
  keandalan; letakkan kamera agar wajah minimal ±80 piksel.
- Topeng 3D dan video *deepfake* berkualitas tinggi di luar cakupan model ini.
- Ambang perlu dikalibrasi dengan data kelas JTEIT sebelum dipakai resmi.

## Deteksi wajah hidup aktif (verifikasi gerak kepala)

Anti-spoofing pasif tidak dapat membedakan gambar yang dikirim lewat *kamera virtual*.
Verifikasi aktif menutup celah itu: server memberi **urutan acak** (menghadap depan,
lalu tiga kali toleh kiri/kanan, masing-masing kembali ke depan; 8 kemungkinan urutan),
dan hanya maju bila arah kepala pada bingkai sesuai urutan. Arah dihitung dari 5 titik
wajah YuNet (posisi hidung terhadap kedua mata), jadi tidak perlu model tambahan.

Selama verifikasi server juga memeriksa: tepat satu wajah, **orang yang sama** dari awal
sampai akhir, anti-spoofing pasif pada bingkai menghadap depan, batas 45 detik, dan
tantangan hanya sekali pakai. Arah toleh yang salah langsung menggagalkan tantangan.

| Penggunaan | Cara |
|---|---|
| **Mode kios** (presensi mandiri bergiliran di laptop/tablet) | Detail sesi → *Mode kios (verifikasi gerak)* |
| **Sesi wajib verifikasi gerak** | Centang saat membuka sesi; kamera kelas pasif ditolak (409) |
| **Pendaftaran wajah** | Dialog foto → *Simpan dengan verifikasi gerak*; set `WAJIB_HIDUP_AKTIF_DAFTAR=1` agar unggah foto biasa ditolak |

Daftar hadir mencatat metode presensi (`kamera` atau `aktif`).

> Ambang arah (|yaw| < 0,15 = depan, > 0,35 = menoleh) diturunkan dari foto uji dan
> perlu dikalibrasi pada uji coba kelas nyata (pencahayaan, jarak, posisi kamera).

## Konfigurasi (variabel lingkungan)

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `DB_PATH` | `data/presensi.db` | Lokasi basis data SQLite |
| `MODEL_DIR` | `models` | Folder model ONNX |
| `AMBANG_KEMIRIPAN` | `0.363` | Ambang kosinus SFace; naikkan untuk mengurangi salah kenal |
| `AMBANG_DETEKSI` | `0.8` | Skor minimum deteksi wajah |
| `API_KEY` | (kosong) | Kunci perangkat kamera kelas (`X-API-Key`), hanya untuk mengirim bingkai presensi |
| `WAJIB_HIDUP_AKTIF_DAFTAR` | `0` | `1` = pendaftaran wajah hanya lewat verifikasi gerak kepala |
| `COOKIE_AMAN` | otomatis | `1` memaksa cookie `Secure` (mis. di balik *reverse proxy* HTTPS), `0` mematikan |
| `ANTI_SPOOFING` | `1` | `0` untuk menonaktifkan deteksi wajah hidup |
| `AMBANG_HIDUP` | `0.5` | Probabilitas "asli" minimum; naikkan agar lebih ketat |
| `TZ` | — | Disarankan `Asia/Makassar` agar waktu presensi sesuai WITA |

## Pengujian

```bash
pytest -q
```

Uji integrasi model asli berjalan bila model sudah diunduh dan folder
`data/foto_uji/` berisi `orang_a.jpg` dan `orang_b.jpg` (dua orang berbeda) serta
`asli.jpg` dan `palsu.jpg` (wajah langsung vs. foto/layar yang difoto ulang).

## Pelindungan data pribadi

Wajah adalah **data pribadi yang bersifat spesifik** menurut UU No. 27 Tahun 2022
tentang Pelindungan Data Pribadi. Karena itu:

- Pendaftaran wajah **wajib** disertai persetujuan eksplisit mahasiswa (dicatat waktunya);
  data KRS tanpa persetujuan tidak dapat dipakai untuk mendaftarkan wajah.
- Persetujuan dapat dicabut kapan saja; data wajah langsung dihapus.
- Yang disimpan hanya **embedding** numerik, bukan foto wajah.
- `DELETE /mahasiswa/{nim}` menghapus identitas, embedding, dan riwayat presensi.
- Folder `data/`, berkas `*.db`, dan model **tidak** masuk repositori (`.gitignore`).
- Setiap akses data memerlukan login; hanya admin yang dapat menghapus data mahasiswa.
- Untuk penerapan di luar jaringan lokal, gunakan HTTPS.

## Lisensi model pihak ketiga

- YuNet, SFace — OpenCV Zoo (Apache-2.0/MIT sesuai kartu model)
- MiniFASNet — Minivision Silent-Face-Anti-Spoofing (Apache-2.0); ONNX dari yakhyo/face-anti-spoofing (Apache-2.0)

## Peta jalan

- [x] Deteksi wajah hidup pasif (*anti-spoofing*) untuk menolak foto/layar ponsel
- [x] Deteksi hidup aktif (gerak kepala acak) untuk mode kios dan pendaftaran wajah
- [x] Dasbor web dosen (sesi, kamera peramban, pendaftaran wajah, audit, rekap per MK)
- [x] Impor peserta kelas (KRS) dari CSV
- [ ] Integrasi langsung SIAKAD UNM dan ekspor ke format rekap jurusan
- [x] Autentikasi per pengguna (dosen, admin jurusan)
- [ ] Evaluasi akurasi pada data mahasiswa JTEIT (TAR/FAR, variasi pencahayaan kelas)
