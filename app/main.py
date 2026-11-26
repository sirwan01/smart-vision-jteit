"""API Presensi Otomatis Berbasis Wajah - JTEIT FT UNM."""

import csv
import hmac
import io
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from app import auth, db, hidup_aktif
from app.auth import Pengguna
from app.config import Settings, get_settings
from app.face_engine import FaceEngine, cocokkan, decode_gambar

STATIK = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Smart Vision JTEIT - Presensi Wajah",
    description="Presensi otomatis berbasis pengenalan wajah, "
    "Jurusan Teknik Elektronika dan Teknologi Informasi, FT UNM.",
    version="0.2.0",
)


# ---------- dependensi ----------

@lru_cache
def settings() -> Settings:
    return get_settings()


@lru_cache
def get_engine() -> FaceEngine:
    s = settings()
    return FaceEngine(s.model_dir, s.ambang_deteksi, s.anti_spoofing)


def tampak_palsu(w) -> bool:
    return w.skor_hidup is not None and w.skor_hidup < settings().ambang_hidup


def bulat(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def get_db():
    conn = db.connect(settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


def pengguna_login(request: Request, conn=Depends(get_db)) -> Pengguna:
    p = auth.pengguna_dari_token(conn, request.cookies.get(auth.NAMA_COOKIE))
    if p is None:
        raise HTTPException(401, "Silakan masuk terlebih dahulu")
    return p


def wajib_admin(p: Pengguna = Depends(pengguna_login)) -> Pengguna:
    if not p.admin:
        raise HTTPException(403, "Hanya admin jurusan")
    return p


def pengirim_presensi(
    request: Request,
    x_api_key: str | None = Header(default=None),
    conn=Depends(get_db),
) -> Pengguna | None:
    """Pengirim bingkai: pengguna login, ATAU perangkat kamera dengan kunci perangkat."""
    kunci = settings().api_key
    if kunci and x_api_key and hmac.compare_digest(x_api_key, kunci):
        return None  # perangkat kamera
    return pengguna_login(request, conn)


Login = Depends(pengguna_login)
Admin = Depends(wajib_admin)


# ---------- skema ----------

class MahasiswaBaru(BaseModel):
    nim: str = Field(min_length=5, max_length=20)
    nama: str = Field(min_length=1)
    prodi: str = Field(min_length=1)
    persetujuan: bool = Field(
        description="Persetujuan eksplisit pemrosesan data biometrik (UU PDP No. 27/2022)"
    )


class SesiBaru(BaseModel):
    ruang: str = Field(min_length=1)
    wajib_hidup_aktif: bool = False  # hanya terima presensi lewat mode kios (gerak kepala)
    kelas_id: int | None = None  # bila diisi, kode & nama MK diambil dari kelas
    kode_mk: str | None = None
    nama_mk: str | None = None


class KelasBaru(BaseModel):
    kode_mk: str = Field(min_length=1, max_length=20)
    nama_mk: str = Field(min_length=1)
    nama_kelas: str = Field(min_length=1, max_length=20)
    semester: str = Field(min_length=1, max_length=30, examples=["2026/2027 Ganjil"])
    dosen_id: int | None = None  # hanya admin; bawaan = pembuat


class DataMasuk(BaseModel):
    username: str
    sandi: str


class GantiSandi(BaseModel):
    sandi_lama: str
    sandi_baru: str


class PenggunaBaru(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")
    nama: str = Field(min_length=1)
    peran: Literal["admin", "dosen"] = "dosen"
    sandi: str


class UbahPengguna(BaseModel):
    nama: str | None = None
    peran: Literal["admin", "dosen"] | None = None
    aktif: bool | None = None
    sandi: str | None = None  # reset kata sandi oleh admin


async def baca_gambar(berkas: UploadFile):
    try:
        return decode_gambar(await berkas.read())
    except ValueError as e:
        raise HTTPException(400, str(e))


def ambil_sesi(conn: sqlite3.Connection, sesi_id: int, p: Pengguna | None) -> sqlite3.Row:
    """Ambil sesi bila boleh diakses: admin/perangkat semua sesi, dosen hanya miliknya.

    Sesi milik dosen lain dijawab 404 agar keberadaannya tidak bocor.
    """
    sesi = conn.execute(
        "SELECT s.*, k.nama_kelas, k.semester FROM sesi s LEFT JOIN kelas k ON k.id = s.kelas_id "
        "WHERE s.id = ?", (sesi_id,)
    ).fetchone()
    if sesi is None or (p is not None and not p.admin and sesi["dosen_id"] != p.id):
        raise HTTPException(404, "Sesi tidak ditemukan")
    return sesi


def ambil_kelas(conn: sqlite3.Connection, kelas_id: int, p: Pengguna) -> sqlite3.Row:
    k = conn.execute(
        "SELECT k.*, u.nama AS dosen FROM kelas k JOIN pengguna u ON u.id = k.dosen_id "
        "WHERE k.id = ?", (kelas_id,)
    ).fetchone()
    if k is None or (not p.admin and k["dosen_id"] != p.id):
        raise HTTPException(404, "Kelas tidak ditemukan")
    return k


def filter_pemilik(p: Pengguna, alias: str = "s") -> tuple[str, list]:
    return ("1 = 1", []) if p.admin else (f"{alias}.dosen_id = ?", [p.id])


# ---------- umum ----------

@app.get("/kesehatan")
def kesehatan():
    return {"status": "ok", "pendaftaran_wajib_aktif": settings().wajib_hidup_aktif_daftar}


@app.get("/", include_in_schema=False)
def beranda():
    return RedirectResponse("/dasbor")


@app.get("/dasbor", include_in_schema=False)
def dasbor():
    """Dasbor web dosen (satu halaman; data diambil lewat API dengan cookie login)."""
    return FileResponse(STATIK / "dasbor.html", media_type="text/html")


# ---------- autentikasi ----------

@app.post("/auth/masuk")
def masuk(data: DataMasuk, request: Request, response: Response, conn=Depends(get_db)):
    p = auth.masuk(conn, data.username.strip(), data.sandi)
    response.set_cookie(
        auth.NAMA_COOKIE,
        auth.buat_token(conn, p.id),
        max_age=auth.UMUR_SESI_DETIK,
        httponly=True,
        samesite="strict",
        secure=auth.cookie_aman(request, settings().cookie_aman),
        path="/",
    )
    return p.publik()


@app.post("/auth/keluar")
def keluar(request: Request, response: Response, conn=Depends(get_db)):
    token = request.cookies.get(auth.NAMA_COOKIE)
    if token:
        auth.hapus_token(conn, token)
    response.delete_cookie(auth.NAMA_COOKIE, path="/")
    return {"keluar": True}


@app.get("/auth/saya")
def saya(p: Pengguna = Login):
    return p.publik()


@app.post("/auth/ganti-sandi")
def ganti_sandi(data: GantiSandi, request: Request, p: Pengguna = Login, conn=Depends(get_db)):
    lama = conn.execute("SELECT hash_sandi FROM pengguna WHERE id = ?", (p.id,)).fetchone()
    if not auth.cocok_sandi(data.sandi_lama, lama["hash_sandi"]):
        raise HTTPException(403, "Kata sandi lama salah")
    auth.periksa_kekuatan(data.sandi_baru)
    conn.execute("UPDATE pengguna SET hash_sandi = ? WHERE id = ?",
                 (auth.hash_sandi(data.sandi_baru), p.id))
    # Keluarkan semua sesi lain milik pengguna ini.
    token = request.cookies.get(auth.NAMA_COOKIE)
    conn.execute("DELETE FROM token_sesi WHERE pengguna_id = ? AND hash != ?",
                 (p.id, auth._hash_token(token or "")))
    conn.commit()
    return {"diubah": True}


# ---------- pengguna (admin) ----------

@app.get("/pengguna", dependencies=[Admin])
def daftar_pengguna(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT u.id, u.username, u.nama, u.peran, u.aktif, u.dibuat, "
        "(SELECT COUNT(*) FROM sesi s WHERE s.dosen_id = u.id) AS jumlah_sesi "
        "FROM pengguna u ORDER BY u.peran, u.nama"
    ).fetchall()
    return [{**dict(r), "aktif": bool(r["aktif"])} for r in rows]


@app.post("/pengguna", status_code=201, dependencies=[Admin])
def tambah_pengguna(data: PenggunaBaru, conn=Depends(get_db)):
    auth.periksa_kekuatan(data.sandi)
    try:
        cur = conn.execute(
            "INSERT INTO pengguna (username, nama, peran, hash_sandi) VALUES (?, ?, ?, ?)",
            (data.username, data.nama, data.peran, auth.hash_sandi(data.sandi)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Nama pengguna {data.username} sudah dipakai")
    return {"id": cur.lastrowid, "username": data.username, "nama": data.nama,
            "peran": data.peran, "aktif": True}


@app.patch("/pengguna/{pengguna_id}")
def ubah_pengguna(pengguna_id: int, data: UbahPengguna,
                  admin: Pengguna = Admin, conn=Depends(get_db)):
    if conn.execute("SELECT 1 FROM pengguna WHERE id = ?", (pengguna_id,)).fetchone() is None:
        raise HTTPException(404, "Pengguna tidak ditemukan")
    if pengguna_id == admin.id and (data.aktif is False or data.peran == "dosen"):
        raise HTTPException(409, "Tidak dapat menonaktifkan atau menurunkan peran akun sendiri")
    if data.nama is not None:
        conn.execute("UPDATE pengguna SET nama = ? WHERE id = ?", (data.nama, pengguna_id))
    if data.peran is not None:
        conn.execute("UPDATE pengguna SET peran = ? WHERE id = ?", (data.peran, pengguna_id))
    if data.aktif is not None:
        conn.execute("UPDATE pengguna SET aktif = ? WHERE id = ?", (int(data.aktif), pengguna_id))
    if data.sandi is not None:
        auth.periksa_kekuatan(data.sandi)
        conn.execute("UPDATE pengguna SET hash_sandi = ? WHERE id = ?",
                     (auth.hash_sandi(data.sandi), pengguna_id))
    if data.aktif is False or data.sandi is not None or data.peran is not None:
        conn.execute("DELETE FROM token_sesi WHERE pengguna_id = ?", (pengguna_id,))
    conn.commit()
    r = conn.execute("SELECT id, username, nama, peran, aktif FROM pengguna WHERE id = ?",
                     (pengguna_id,)).fetchone()
    return {**dict(r), "aktif": bool(r["aktif"])}


# ---------- mahasiswa ----------

@app.post("/mahasiswa", status_code=201, dependencies=[Login])
def daftar_mahasiswa(m: MahasiswaBaru, conn=Depends(get_db)):
    if not m.persetujuan:
        raise HTTPException(422, "Pendaftaran wajah memerlukan persetujuan mahasiswa")
    try:
        conn.execute(
            "INSERT INTO mahasiswa (nim, nama, prodi, persetujuan_pada) "
            "VALUES (?, ?, ?, datetime('now', 'localtime'))",
            (m.nim, m.nama, m.prodi),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"NIM {m.nim} sudah terdaftar")
    return {"nim": m.nim, "nama": m.nama, "prodi": m.prodi}


@app.get("/mahasiswa", dependencies=[Login])
def daftar_semua_mahasiswa(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, m.persetujuan_pada, COUNT(w.id) AS jumlah_wajah "
        "FROM mahasiswa m LEFT JOIN wajah w ON w.nim = m.nim "
        "GROUP BY m.nim ORDER BY m.nim"
    ).fetchall()
    return [{**dict(r), "persetujuan": r["persetujuan_pada"] is not None} for r in rows]


@app.post("/mahasiswa/{nim}/persetujuan", dependencies=[Login])
def catat_persetujuan(nim: str, conn=Depends(get_db)):
    """Catat persetujuan biometrik mahasiswa (mis. yang masuk lewat impor KRS)."""
    if conn.execute("SELECT 1 FROM mahasiswa WHERE nim = ?", (nim,)).fetchone() is None:
        raise HTTPException(404, "Mahasiswa tidak ditemukan")
    conn.execute(
        "UPDATE mahasiswa SET persetujuan_pada = datetime('now', 'localtime') "
        "WHERE nim = ? AND persetujuan_pada IS NULL", (nim,)
    )
    conn.commit()
    return {"nim": nim, "persetujuan": True}


@app.delete("/mahasiswa/{nim}/persetujuan", dependencies=[Login])
def cabut_persetujuan(nim: str, conn=Depends(get_db)):
    """Pencabutan persetujuan: data wajah dihapus, identitas & riwayat presensi tetap."""
    if conn.execute("SELECT 1 FROM mahasiswa WHERE nim = ?", (nim,)).fetchone() is None:
        raise HTTPException(404, "Mahasiswa tidak ditemukan")
    terhapus = conn.execute("DELETE FROM wajah WHERE nim = ?", (nim,)).rowcount
    conn.execute("UPDATE mahasiswa SET persetujuan_pada = NULL WHERE nim = ?", (nim,))
    conn.commit()
    return {"nim": nim, "persetujuan": False, "wajah_dihapus": terhapus}


@app.delete("/mahasiswa/{nim}", dependencies=[Admin])
def hapus_mahasiswa(nim: str, conn=Depends(get_db)):
    """Hak penghapusan data (admin): identitas, embedding wajah, dan riwayat presensi."""
    cur = conn.execute("DELETE FROM mahasiswa WHERE nim = ?", (nim,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Mahasiswa tidak ditemukan")
    return {"dihapus": nim}


@app.post("/mahasiswa/{nim}/wajah", status_code=201, dependencies=[Login])
async def daftar_wajah(
    nim: str,
    berkas: UploadFile = File(...),
    conn=Depends(get_db),
    engine: FaceEngine = Depends(get_engine),
):
    m = conn.execute("SELECT persetujuan_pada FROM mahasiswa WHERE nim = ?", (nim,)).fetchone()
    if m is None:
        raise HTTPException(404, "Mahasiswa tidak ditemukan; daftarkan dulu")
    if m["persetujuan_pada"] is None:
        raise HTTPException(403, "Mahasiswa belum memberi persetujuan biometrik; catat dulu persetujuannya")
    if settings().wajib_hidup_aktif_daftar:
        raise HTTPException(403, "Pendaftaran wajah wajib melalui verifikasi gerak kepala")
    wajah = engine.analisis(await baca_gambar(berkas))
    if len(wajah) != 1:
        raise HTTPException(
            422, f"Foto pendaftaran harus berisi tepat satu wajah (terdeteksi {len(wajah)})"
        )
    if tampak_palsu(wajah[0]):
        raise HTTPException(
            422,
            f"Wajah terdeteksi sebagai foto/layar, bukan wajah langsung "
            f"(skor hidup {wajah[0].skor_hidup:.3f}). Ambil foto langsung dari kamera.",
        )
    conn.execute(
        "INSERT INTO wajah (nim, embedding) VALUES (?, ?)",
        (nim, wajah[0].embedding.tobytes()),
    )
    conn.commit()
    jumlah = conn.execute("SELECT COUNT(*) FROM wajah WHERE nim = ?", (nim,)).fetchone()[0]
    return {"nim": nim, "jumlah_wajah": jumlah}


# ---------- deteksi hidup aktif (tantangan gerak kepala) ----------

class TantanganBaru(BaseModel):
    tujuan: Literal["presensi", "pendaftaran"]
    sesi_id: int | None = None   # untuk presensi (mode kios)
    nim: str | None = None       # untuk pendaftaran wajah


def _pemilik(p: Pengguna | None) -> str:
    return "perangkat" if p is None else f"pengguna:{p.id}"


def _cek_target(conn, data_tujuan: str, target: str, p: Pengguna | None) -> None:
    if data_tujuan == "presensi":
        if ambil_sesi(conn, int(target), p)["ditutup"] is not None:
            raise HTTPException(409, "Sesi sudah ditutup")
    else:
        if p is None:
            raise HTTPException(401, "Pendaftaran wajah memerlukan login dosen/admin")
        m = conn.execute("SELECT persetujuan_pada FROM mahasiswa WHERE nim = ?", (target,)).fetchone()
        if m is None:
            raise HTTPException(404, "Mahasiswa tidak ditemukan")
        if m["persetujuan_pada"] is None:
            raise HTTPException(403, "Mahasiswa belum memberi persetujuan biometrik")


@app.post("/hidup/tantangan", status_code=201)
def buat_tantangan(data: TantanganBaru, p: Pengguna | None = Depends(pengirim_presensi),
                   conn=Depends(get_db)):
    """Buat tantangan gerak kepala acak (berlaku 45 detik, sekali pakai).

    Presensi: oleh pemilik sesi/admin atau perangkat kamera. Pendaftaran: login saja.
    """
    target = data.sesi_id if data.tujuan == "presensi" else data.nim
    if target is None:
        raise HTTPException(422, "Isi sesi_id untuk presensi atau nim untuk pendaftaran")
    _cek_target(conn, data.tujuan, str(target), p)
    return hidup_aktif.pengelola.buat(data.tujuan, str(target), _pemilik(p)).publik()


@app.post("/hidup/tantangan/{tantangan_id}/bingkai")
async def kirim_bingkai_tantangan(
    tantangan_id: str,
    berkas: UploadFile = File(...),
    p: Pengguna | None = Depends(pengirim_presensi),
    conn=Depends(get_db),
    engine: FaceEngine = Depends(get_engine),
):
    """Nilai satu bingkai; bila urutan selesai, presensi/pendaftaran langsung diproses."""
    t = hidup_aktif.pengelola.ambil(tantangan_id, _pemilik(p))
    if t is None:
        raise HTTPException(404, "Tantangan tidak ditemukan atau kedaluwarsa")
    wajah = engine.analisis(await baca_gambar(berkas))
    with hidup_aktif.pengelola.kunci:
        hasil = hidup_aktif.proses_bingkai(t, wajah, settings().ambang_hidup)
        selesaikan = t.status == "lulus" and not t.dipakai
        if selesaikan:
            t.dipakai = True
    if not selesaikan:
        return hasil
    _cek_target(conn, t.tujuan, t.target, p)
    emb, hidup = t.embedding_akhir(), t.skor_hidup_akhir()
    if t.tujuan == "pendaftaran":
        conn.execute("INSERT INTO wajah (nim, embedding) VALUES (?, ?)", (t.target, emb.tobytes()))
        conn.commit()
        jumlah = conn.execute("SELECT COUNT(*) FROM wajah WHERE nim = ?", (t.target,)).fetchone()[0]
        return {**hasil, "hasil": {"nim": t.target, "jumlah_wajah": jumlah}}
    galeri, label = db.muat_galeri(conn)
    nim, skor = cocokkan(emb, galeri, label, settings().ambang_kemiripan)
    if nim is None:
        return {**hasil, "hasil": {"dikenali": False, "skor_terbaik": round(skor, 3)}}
    baru = conn.execute(
        "INSERT OR IGNORE INTO presensi (sesi_id, nim, skor, skor_hidup, metode) "
        "VALUES (?, ?, ?, ?, 'aktif')",
        (int(t.target), nim, skor, hidup),
    ).rowcount == 1
    conn.commit()
    nama = conn.execute("SELECT nama FROM mahasiswa WHERE nim = ?", (nim,)).fetchone()["nama"]
    return {**hasil, "hasil": {"dikenali": True, "nim": nim, "nama": nama,
                               "skor": round(skor, 3), "baru_dicatat": baru}}


# ---------- kelas & KRS ----------

NIM_SAH = re.compile(r"^[A-Za-z0-9]{5,20}$")
KOLOM_NAMA = {"nama", "nama mahasiswa", "nama_mahasiswa", "nama lengkap"}
KOLOM_PRODI = {"prodi", "program studi", "program_studi"}
UKURAN_CSV_MAKS = 1_000_000


def baca_csv_krs(data: bytes) -> list[dict]:
    """Baca CSV KRS (koma/titik koma/tab, UTF-8 atau Latin-1, BOM Excel diabaikan)."""
    try:
        teks = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        teks = data.decode("latin-1")
    baris_pertama = teks.splitlines()[0] if teks.strip() else ""
    pemisah = max(",;\t", key=baris_pertama.count)
    pembaca = csv.DictReader(io.StringIO(teks), delimiter=pemisah)
    if not pembaca.fieldnames:
        raise HTTPException(422, "Berkas CSV kosong")
    kolom = {k: (k or "").strip().lower() for k in pembaca.fieldnames}
    if "nim" not in kolom.values():
        raise HTTPException(422, "Kolom 'nim' tidak ditemukan pada baris judul CSV")
    hasil = []
    for baris in pembaca:
        b = {kolom[k]: (v or "").strip() for k, v in baris.items() if k is not None}
        hasil.append({
            "nim": b.get("nim", ""),
            "nama": next((b[k] for k in KOLOM_NAMA if b.get(k)), ""),
            "prodi": next((b[k] for k in KOLOM_PRODI if b.get(k)), ""),
        })
    return hasil


@app.post("/kelas", status_code=201)
def buat_kelas(k: KelasBaru, p: Pengguna = Login, conn=Depends(get_db)):
    dosen_id = p.id
    if k.dosen_id is not None and k.dosen_id != p.id:
        if not p.admin:
            raise HTTPException(403, "Hanya admin yang dapat membuat kelas untuk dosen lain")
        if conn.execute("SELECT 1 FROM pengguna WHERE id = ? AND aktif = 1",
                        (k.dosen_id,)).fetchone() is None:
            raise HTTPException(422, "Dosen pengampu tidak ditemukan")
        dosen_id = k.dosen_id
    try:
        cur = conn.execute(
            "INSERT INTO kelas (kode_mk, nama_mk, nama_kelas, semester, dosen_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (k.kode_mk.strip().upper(), k.nama_mk.strip(), k.nama_kelas.strip().upper(),
             k.semester.strip(), dosen_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Kelas dengan kode MK, nama kelas, dan semester itu sudah ada")
    return dict(ambil_kelas(conn, cur.lastrowid, p))


@app.get("/kelas")
def daftar_kelas(p: Pengguna = Login, conn=Depends(get_db)):
    milik, arg = filter_pemilik(p, "k")
    rows = conn.execute(
        "SELECT k.*, u.nama AS dosen, "
        "(SELECT COUNT(*) FROM kelas_peserta x WHERE x.kelas_id = k.id) AS jumlah_peserta, "
        "(SELECT COUNT(*) FROM sesi s WHERE s.kelas_id = k.id) AS jumlah_sesi "
        f"FROM kelas k JOIN pengguna u ON u.id = k.dosen_id WHERE {milik} "
        "ORDER BY k.semester DESC, k.kode_mk, k.nama_kelas",
        arg,
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/kelas/{kelas_id}/peserta")
def peserta_kelas(kelas_id: int, p: Pengguna = Login, conn=Depends(get_db)):
    ambil_kelas(conn, kelas_id, p)
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, m.persetujuan_pada IS NOT NULL AS persetujuan, "
        "(SELECT COUNT(*) FROM wajah w WHERE w.nim = m.nim) AS jumlah_wajah "
        "FROM kelas_peserta x JOIN mahasiswa m ON m.nim = x.nim "
        "WHERE x.kelas_id = ? ORDER BY m.nim",
        (kelas_id,),
    ).fetchall()
    return [{**dict(r), "persetujuan": bool(r["persetujuan"])} for r in rows]


@app.post("/kelas/{kelas_id}/peserta/impor")
async def impor_peserta(kelas_id: int, berkas: UploadFile = File(...),
                        p: Pengguna = Login, conn=Depends(get_db)):
    """Impor KRS dari CSV berkolom `nim` (wajib), `nama`, `prodi`.

    Mahasiswa yang belum ada dibuat sebagai identitas saja, TANPA persetujuan
    biometrik; wajahnya baru dapat didaftarkan setelah persetujuan dicatat.
    """
    ambil_kelas(conn, kelas_id, p)
    data = await berkas.read(UKURAN_CSV_MAKS + 1)
    if len(data) > UKURAN_CSV_MAKS:
        raise HTTPException(413, "Berkas CSV terlalu besar (maks. 1 MB)")
    ditambahkan, sudah_ada, mahasiswa_baru, ditolak = 0, 0, 0, []
    for i, b in enumerate(baca_csv_krs(data), start=2):  # baris 1 = judul
        if not any(b.values()):
            continue
        if not NIM_SAH.match(b["nim"]):
            ditolak.append({"baris": i, "nim": b["nim"], "alasan": "NIM tidak valid"})
            continue
        if conn.execute("SELECT 1 FROM mahasiswa WHERE nim = ?", (b["nim"],)).fetchone() is None:
            if not b["nama"]:
                ditolak.append({"baris": i, "nim": b["nim"],
                                "alasan": "Mahasiswa baru memerlukan kolom nama"})
                continue
            conn.execute("INSERT INTO mahasiswa (nim, nama, prodi) VALUES (?, ?, ?)",
                         (b["nim"], b["nama"], b["prodi"] or "-"))
            mahasiswa_baru += 1
        if conn.execute("INSERT OR IGNORE INTO kelas_peserta (kelas_id, nim) VALUES (?, ?)",
                        (kelas_id, b["nim"])).rowcount:
            ditambahkan += 1
        else:
            sudah_ada += 1
    conn.commit()
    return {"ditambahkan": ditambahkan, "sudah_ada": sudah_ada,
            "mahasiswa_baru": mahasiswa_baru, "ditolak": ditolak}


@app.delete("/kelas/{kelas_id}/peserta/{nim}")
def keluarkan_peserta(kelas_id: int, nim: str, p: Pengguna = Login, conn=Depends(get_db)):
    ambil_kelas(conn, kelas_id, p)
    cur = conn.execute("DELETE FROM kelas_peserta WHERE kelas_id = ? AND nim = ?", (kelas_id, nim))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Mahasiswa bukan peserta kelas ini")
    return {"dikeluarkan": nim}


@app.get("/kelas/{kelas_id}/rekap")
def rekap_kelas(kelas_id: int, batas_persen: float = 75.0,
                p: Pengguna = Login, conn=Depends(get_db)):
    """Rekap seluruh peserta KRS (termasuk yang tidak pernah hadir)."""
    k = ambil_kelas(conn, kelas_id, p)
    total = conn.execute("SELECT COUNT(*) FROM sesi WHERE kelas_id = ?", (kelas_id,)).fetchone()[0]
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, "
        "(SELECT COUNT(*) FROM presensi x JOIN sesi s ON s.id = x.sesi_id "
        " WHERE s.kelas_id = ? AND x.nim = m.nim) AS hadir "
        "FROM kelas_peserta kp JOIN mahasiswa m ON m.nim = kp.nim "
        "WHERE kp.kelas_id = ? ORDER BY m.nim",
        (kelas_id, kelas_id),
    ).fetchall()
    peserta = []
    for r in rows:
        persen = round(100 * r["hadir"] / total, 1) if total else 0.0
        peserta.append({**dict(r), "persen": persen,
                        "memenuhi": total > 0 and persen >= batas_persen})
    luar = conn.execute(
        "SELECT m.nim, m.nama, COUNT(*) AS hadir FROM presensi x "
        "JOIN sesi s ON s.id = x.sesi_id JOIN mahasiswa m ON m.nim = x.nim "
        "WHERE s.kelas_id = ? AND x.nim NOT IN "
        "(SELECT nim FROM kelas_peserta WHERE kelas_id = ?) GROUP BY m.nim ORDER BY m.nim",
        (kelas_id, kelas_id),
    ).fetchall()
    return {"kelas": dict(k), "jumlah_sesi": total, "batas_persen": batas_persen,
            "peserta": peserta, "hadir_di_luar_krs": [dict(r) for r in luar]}


# ---------- sesi ----------

@app.post("/sesi", status_code=201)
def buka_sesi(s: SesiBaru, p: Pengguna = Login, conn=Depends(get_db)):
    """Sesi dimiliki dosen yang membukanya; nama dosen diambil dari akun.

    Dengan `kelas_id`, kode/nama MK diambil dari kelas (kelas harus milik dosen ini).
    """
    if s.kelas_id is not None:
        k = ambil_kelas(conn, s.kelas_id, p)
        kode, nama = k["kode_mk"], k["nama_mk"]
    else:
        kode, nama = (s.kode_mk or "").strip(), (s.nama_mk or "").strip()
        if not kode or not nama:
            raise HTTPException(422, "Isi kode & nama MK, atau pilih kelas")
    cur = conn.execute(
        "INSERT INTO sesi (kode_mk, nama_mk, ruang, dosen, dosen_id, kelas_id, wajib_hidup_aktif) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kode, nama, s.ruang, p.nama, p.id, s.kelas_id, int(s.wajib_hidup_aktif)),
    )
    conn.commit()
    return dict(ambil_sesi(conn, cur.lastrowid, p))


@app.get("/sesi")
def daftar_sesi(aktif: bool | None = None, kode_mk: str | None = None,
                p: Pengguna = Login, conn=Depends(get_db)):
    """Daftar sesi terbaru (dosen: miliknya; admin: semua) dengan jumlah hadir dan palsu."""
    syarat, arg = filter_pemilik(p)
    syarat = [syarat]
    if aktif is not None:
        syarat.append("s.ditutup IS NULL" if aktif else "s.ditutup IS NOT NULL")
    if kode_mk:
        syarat.append("s.kode_mk = ?")
        arg.append(kode_mk)
    rows = conn.execute(
        "SELECT s.*, k.nama_kelas, "
        "(SELECT COUNT(*) FROM presensi p WHERE p.sesi_id = s.id) AS jumlah_hadir, "
        "(SELECT COUNT(*) FROM kelas_peserta kp WHERE kp.kelas_id = s.kelas_id) AS jumlah_peserta, "
        "(SELECT COUNT(*) FROM percobaan_palsu f WHERE f.sesi_id = s.id) AS jumlah_palsu "
        f"FROM sesi s LEFT JOIN kelas k ON k.id = s.kelas_id "
        f"WHERE {' AND '.join(syarat)} ORDER BY s.id DESC LIMIT 200",
        arg,
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/ringkasan")
def ringkasan(p: Pengguna = Login, conn=Depends(get_db)):
    milik, arg = filter_pemilik(p)

    def q(sql, a=()):
        return conn.execute(sql, a).fetchone()[0]

    return {
        "jumlah_mahasiswa": q("SELECT COUNT(*) FROM mahasiswa"),
        "mahasiswa_tanpa_wajah": q(
            "SELECT COUNT(*) FROM mahasiswa WHERE nim NOT IN (SELECT nim FROM wajah)"
        ),
        "sesi_aktif": q(f"SELECT COUNT(*) FROM sesi s WHERE s.ditutup IS NULL AND {milik}", arg),
        "hadir_hari_ini": q(
            "SELECT COUNT(*) FROM presensi x JOIN sesi s ON s.id = x.sesi_id "
            f"WHERE date(x.waktu) = date('now', 'localtime') AND {milik}", arg
        ),
        "palsu_hari_ini": q(
            "SELECT COUNT(*) FROM percobaan_palsu x JOIN sesi s ON s.id = x.sesi_id "
            f"WHERE date(x.waktu) = date('now', 'localtime') AND {milik}", arg
        ),
    }


@app.get("/rekap")
def daftar_mata_kuliah(p: Pengguna = Login, conn=Depends(get_db)):
    milik, arg = filter_pemilik(p)
    rows = conn.execute(
        "SELECT s.kode_mk, s.nama_mk, COUNT(*) AS jumlah_sesi FROM sesi s "
        f"WHERE {milik} GROUP BY s.kode_mk ORDER BY s.kode_mk",
        arg,
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/rekap/{kode_mk}")
def rekap_mata_kuliah(kode_mk: str, batas_persen: float = 75.0,
                      p: Pengguna = Login, conn=Depends(get_db)):
    """Rekap kehadiran per mahasiswa untuk satu mata kuliah.

    Dosen: hanya sesi yang ia buka (kelas paralel dosen lain tidak ikut dihitung).
    Peserta = mahasiswa yang pernah tercatat hadir (data KRS belum terhubung).
    `memenuhi` = persentase hadir >= `batas_persen` (umumnya syarat ikut ujian akhir).
    """
    milik, arg = filter_pemilik(p)
    total = conn.execute(
        f"SELECT COUNT(*) FROM sesi s WHERE s.kode_mk = ? AND {milik}", [kode_mk, *arg]
    ).fetchone()[0]
    if total == 0:
        raise HTTPException(404, "Belum ada sesi untuk mata kuliah ini")
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, COUNT(x.id) AS hadir "
        "FROM presensi x JOIN sesi s ON s.id = x.sesi_id JOIN mahasiswa m ON m.nim = x.nim "
        f"WHERE s.kode_mk = ? AND {milik} GROUP BY m.nim ORDER BY m.nim",
        [kode_mk, *arg],
    ).fetchall()
    peserta = []
    for r in rows:
        persen = round(100 * r["hadir"] / total, 1)
        peserta.append({**dict(r), "persen": persen, "memenuhi": persen >= batas_persen})
    return {"kode_mk": kode_mk, "jumlah_sesi": total, "batas_persen": batas_persen,
            "peserta": peserta}


@app.post("/sesi/{sesi_id}/tutup")
def tutup_sesi(sesi_id: int, p: Pengguna = Login, conn=Depends(get_db)):
    ambil_sesi(conn, sesi_id, p)
    conn.execute(
        "UPDATE sesi SET ditutup = datetime('now', 'localtime') "
        "WHERE id = ? AND ditutup IS NULL",
        (sesi_id,),
    )
    conn.commit()
    return dict(ambil_sesi(conn, sesi_id, p))


@app.post("/sesi/{sesi_id}/presensi")
async def presensi_dari_gambar(
    sesi_id: int,
    berkas: UploadFile = File(...),
    p: Pengguna | None = Depends(pengirim_presensi),
    conn=Depends(get_db),
    engine: FaceEngine = Depends(get_engine),
):
    """Kenali semua wajah dalam satu bingkai kamera dan catat kehadirannya.

    Boleh dikirim oleh pemilik sesi/admin (cookie login) atau perangkat kamera (X-API-Key).
    """
    sesi = ambil_sesi(conn, sesi_id, p)
    if sesi["ditutup"] is not None:
        raise HTTPException(409, "Sesi sudah ditutup")
    if sesi["wajib_hidup_aktif"]:
        raise HTTPException(409, "Sesi ini mewajibkan presensi lewat mode kios (verifikasi gerak)")
    wajah = engine.analisis(await baca_gambar(berkas))
    galeri, label = db.muat_galeri(conn)
    ambang = settings().ambang_kemiripan

    dikenali, tidak_dikenali, ditolak_palsu = [], [], []
    for w in wajah:
        nim, skor = cocokkan(w.embedding, galeri, label, ambang)
        if tampak_palsu(w):
            # Foto/layar di depan kamera: tidak dicatat hadir, tetapi diaudit.
            conn.execute(
                "INSERT INTO percobaan_palsu (sesi_id, nim_dugaan, skor_hidup, skor_kemiripan) "
                "VALUES (?, ?, ?, ?)",
                (sesi_id, nim, w.skor_hidup, skor),
            )
            ditolak_palsu.append(
                {"nim_dugaan": nim, "skor_hidup": bulat(w.skor_hidup), "kotak": w.kotak}
            )
            continue
        if nim is None:
            tidak_dikenali.append({"kotak": w.kotak, "skor_terbaik": round(skor, 3)})
            continue
        baru = conn.execute(
            "INSERT OR IGNORE INTO presensi (sesi_id, nim, skor, skor_hidup) VALUES (?, ?, ?, ?)",
            (sesi_id, nim, skor, w.skor_hidup),
        ).rowcount == 1
        dikenali.append(
            {
                "nim": nim,
                "skor": round(skor, 3),
                "skor_hidup": bulat(w.skor_hidup),
                "kotak": w.kotak,
                "baru_dicatat": baru,
            }
        )
    conn.commit()
    return {
        "jumlah_wajah": len(wajah),
        "dikenali": dikenali,
        "tidak_dikenali": tidak_dikenali,
        "ditolak_palsu": ditolak_palsu,
    }


@app.get("/sesi/{sesi_id}/percobaan-palsu")
def daftar_percobaan_palsu(sesi_id: int, p: Pengguna = Login, conn=Depends(get_db)):
    """Audit: wajah yang ditolak sebagai foto/layar, beserta NIM yang diduga dipalsukan."""
    ambil_sesi(conn, sesi_id, p)
    rows = conn.execute(
        "SELECT x.waktu, x.nim_dugaan, m.nama, ROUND(x.skor_hidup, 3) AS skor_hidup, "
        "ROUND(x.skor_kemiripan, 3) AS skor_kemiripan "
        "FROM percobaan_palsu x LEFT JOIN mahasiswa m ON m.nim = x.nim_dugaan "
        "WHERE x.sesi_id = ? ORDER BY x.waktu",
        (sesi_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _rekap(conn: sqlite3.Connection, sesi_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, x.waktu, ROUND(x.skor, 3) AS skor, "
        "ROUND(x.skor_hidup, 3) AS skor_hidup, x.metode "
        "FROM presensi x JOIN mahasiswa m ON m.nim = x.nim "
        "WHERE x.sesi_id = ? ORDER BY x.waktu",
        (sesi_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/sesi/{sesi_id}/presensi")
def rekap_presensi(sesi_id: int, p: Pengguna = Login, conn=Depends(get_db)):
    """Daftar hadir; untuk sesi berkelas juga peserta KRS yang belum hadir."""
    sesi = ambil_sesi(conn, sesi_id, p)
    hasil = {"sesi": dict(sesi), "hadir": _rekap(conn, sesi_id), "belum_hadir": None}
    if sesi["kelas_id"] is not None:
        rows = conn.execute(
            "SELECT m.nim, m.nama, m.prodi, "
            "(SELECT COUNT(*) FROM wajah w WHERE w.nim = m.nim) AS jumlah_wajah "
            "FROM kelas_peserta kp JOIN mahasiswa m ON m.nim = kp.nim "
            "WHERE kp.kelas_id = ? AND kp.nim NOT IN "
            "(SELECT nim FROM presensi WHERE sesi_id = ?) ORDER BY m.nim",
            (sesi["kelas_id"], sesi_id),
        ).fetchall()
        hasil["belum_hadir"] = [dict(r) for r in rows]
    return hasil


@app.get("/sesi/{sesi_id}/presensi.csv")
def rekap_presensi_csv(sesi_id: int, p: Pengguna = Login, conn=Depends(get_db)):
    ambil_sesi(conn, sesi_id, p)
    buf = io.StringIO()
    tulis = csv.DictWriter(buf, fieldnames=["nim", "nama", "prodi", "waktu", "skor", "skor_hidup", "metode"])
    tulis.writeheader()
    tulis.writerows(_rekap(conn, sesi_id))
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=presensi_sesi_{sesi_id}.csv"},
    )
