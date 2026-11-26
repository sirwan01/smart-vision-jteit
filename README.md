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
uvicorn app.main:app --reload          # buka http://localhost:8000/docs
```

Klien kamera (pada komputer yang terhubung ke webcam atau kamera IP):

```bash
python scripts/kamera_presensi.py --sesi 1 --api http://SERVER:8000 --interval 3
```

## Dasbor web dosen

Buka `http://SERVER:8000/dasbor` (atau `/`). Bila `API_KEY` diisi, dasbor meminta kunci
tersebut sekali per tab peramban (disimpan di `sessionStorage`, hilang saat tab ditutup).

| Menu | Fungsi |
|---|---|
| **Sesi** | Ringkasan hari ini, buka/tutup sesi, daftar hadir langsung (segar tiap 5 detik), percobaan palsu, unduh CSV |
| **Kamera kelas dari peramban** | Laptop dosen menjadi kamera presensi: bingkai webcam dikirim tiap 3 detik |
| **Mahasiswa** | Daftarkan mahasiswa (wajib centang persetujuan), ambil foto wajah lewat webcam atau unggah berkas, cari, hapus data |
| **Rekap** | Persentase kehadiran per mata kuliah dengan batas minimal yang dapat diatur (bawaan 75%) |

Webcam di peramban hanya berfungsi melalui **HTTPS** atau `localhost`.

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

## Konfigurasi (variabel lingkungan)

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `DB_PATH` | `data/presensi.db` | Lokasi basis data SQLite |
| `MODEL_DIR` | `models` | Folder model ONNX |
| `AMBANG_KEMIRIPAN` | `0.363` | Ambang kosinus SFace; naikkan untuk mengurangi salah kenal |
| `AMBANG_DETEKSI` | `0.8` | Skor minimum deteksi wajah |
| `API_KEY` | (kosong) | Bila diisi, setiap permintaan wajib membawa header `X-API-Key` |
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

- Pendaftaran wajah **wajib** disertai persetujuan eksplisit mahasiswa (dicatat waktunya).
- Yang disimpan hanya **embedding** numerik, bukan foto wajah.
- `DELETE /mahasiswa/{nim}` menghapus identitas, embedding, dan riwayat presensi.
- Folder `data/`, berkas `*.db`, dan model **tidak** masuk repositori (`.gitignore`).
- Untuk penerapan di luar jaringan lokal, isi `API_KEY` dan gunakan HTTPS.

## Lisensi model pihak ketiga

- YuNet, SFace — OpenCV Zoo (Apache-2.0/MIT sesuai kartu model)
- MiniFASNet — Minivision Silent-Face-Anti-Spoofing (Apache-2.0); ONNX dari yakhyo/face-anti-spoofing (Apache-2.0)

## Peta jalan

- [x] Deteksi wajah hidup pasif (*anti-spoofing*) untuk menolak foto/layar ponsel
- [ ] Deteksi hidup aktif (kedip/gerak kepala) sebagai lapisan kedua
- [x] Dasbor web dosen (sesi, kamera peramban, pendaftaran wajah, audit, rekap per MK)
- [ ] Integrasi SIAKAD UNM dan ekspor ke format rekap jurusan
- [ ] Autentikasi per pengguna (dosen, admin jurusan)
- [ ] Evaluasi akurasi pada data mahasiswa JTEIT (TAR/FAR, variasi pencahayaan kelas)
