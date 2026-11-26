"""API Presensi Otomatis Berbasis Wajah - JTEIT FT UNM."""

import csv
import io
import sqlite3
from functools import lru_cache

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db
from app.config import Settings, get_settings
from app.face_engine import FaceEngine, cocokkan, decode_gambar

app = FastAPI(
    title="Smart Vision JTEIT - Presensi Wajah",
    description="Presensi otomatis berbasis pengenalan wajah, "
    "Jurusan Teknik Elektronika dan Teknologi Informasi, FT UNM.",
    version="0.1.0",
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


def cek_api_key(x_api_key: str | None = Header(default=None)):
    kunci = settings().api_key
    if kunci and x_api_key != kunci:
        raise HTTPException(401, "X-API-Key tidak valid")


Aman = Depends(cek_api_key)


# ---------- skema ----------

class MahasiswaBaru(BaseModel):
    nim: str = Field(min_length=5, max_length=20)
    nama: str = Field(min_length=1)
    prodi: str = Field(min_length=1)
    persetujuan: bool = Field(
        description="Persetujuan eksplisit pemrosesan data biometrik (UU PDP No. 27/2022)"
    )


class SesiBaru(BaseModel):
    kode_mk: str
    nama_mk: str
    ruang: str
    dosen: str


async def baca_gambar(berkas: UploadFile):
    try:
        return decode_gambar(await berkas.read())
    except ValueError as e:
        raise HTTPException(400, str(e))


def ambil_sesi(conn: sqlite3.Connection, sesi_id: int) -> sqlite3.Row:
    sesi = conn.execute("SELECT * FROM sesi WHERE id = ?", (sesi_id,)).fetchone()
    if sesi is None:
        raise HTTPException(404, "Sesi tidak ditemukan")
    return sesi


# ---------- endpoint ----------

@app.get("/kesehatan")
def kesehatan():
    return {"status": "ok"}


@app.post("/mahasiswa", status_code=201, dependencies=[Aman])
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


@app.get("/mahasiswa", dependencies=[Aman])
def daftar_semua_mahasiswa(conn=Depends(get_db)):
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, COUNT(w.id) AS jumlah_wajah "
        "FROM mahasiswa m LEFT JOIN wajah w ON w.nim = m.nim "
        "GROUP BY m.nim ORDER BY m.nim"
    ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/mahasiswa/{nim}", dependencies=[Aman])
def hapus_mahasiswa(nim: str, conn=Depends(get_db)):
    """Hak penghapusan data: menghapus identitas, embedding wajah, dan riwayat presensi."""
    cur = conn.execute("DELETE FROM mahasiswa WHERE nim = ?", (nim,))
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "Mahasiswa tidak ditemukan")
    return {"dihapus": nim}


@app.post("/mahasiswa/{nim}/wajah", status_code=201, dependencies=[Aman])
async def daftar_wajah(
    nim: str,
    berkas: UploadFile = File(...),
    conn=Depends(get_db),
    engine: FaceEngine = Depends(get_engine),
):
    if conn.execute("SELECT 1 FROM mahasiswa WHERE nim = ?", (nim,)).fetchone() is None:
        raise HTTPException(404, "Mahasiswa tidak ditemukan; daftarkan dulu")
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


@app.post("/sesi", status_code=201, dependencies=[Aman])
def buka_sesi(s: SesiBaru, conn=Depends(get_db)):
    cur = conn.execute(
        "INSERT INTO sesi (kode_mk, nama_mk, ruang, dosen) VALUES (?, ?, ?, ?)",
        (s.kode_mk, s.nama_mk, s.ruang, s.dosen),
    )
    conn.commit()
    return dict(ambil_sesi(conn, cur.lastrowid))


@app.post("/sesi/{sesi_id}/tutup", dependencies=[Aman])
def tutup_sesi(sesi_id: int, conn=Depends(get_db)):
    ambil_sesi(conn, sesi_id)
    conn.execute(
        "UPDATE sesi SET ditutup = datetime('now', 'localtime') "
        "WHERE id = ? AND ditutup IS NULL",
        (sesi_id,),
    )
    conn.commit()
    return dict(ambil_sesi(conn, sesi_id))


@app.post("/sesi/{sesi_id}/presensi", dependencies=[Aman])
async def presensi_dari_gambar(
    sesi_id: int,
    berkas: UploadFile = File(...),
    conn=Depends(get_db),
    engine: FaceEngine = Depends(get_engine),
):
    """Kenali semua wajah dalam satu bingkai kamera dan catat kehadirannya."""
    if ambil_sesi(conn, sesi_id)["ditutup"] is not None:
        raise HTTPException(409, "Sesi sudah ditutup")
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


@app.get("/sesi/{sesi_id}/percobaan-palsu", dependencies=[Aman])
def daftar_percobaan_palsu(sesi_id: int, conn=Depends(get_db)):
    """Audit: wajah yang ditolak sebagai foto/layar, beserta NIM yang diduga dipalsukan."""
    ambil_sesi(conn, sesi_id)
    rows = conn.execute(
        "SELECT p.waktu, p.nim_dugaan, m.nama, ROUND(p.skor_hidup, 3) AS skor_hidup, "
        "ROUND(p.skor_kemiripan, 3) AS skor_kemiripan "
        "FROM percobaan_palsu p LEFT JOIN mahasiswa m ON m.nim = p.nim_dugaan "
        "WHERE p.sesi_id = ? ORDER BY p.waktu",
        (sesi_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _rekap(conn: sqlite3.Connection, sesi_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT m.nim, m.nama, m.prodi, p.waktu, ROUND(p.skor, 3) AS skor, "
        "ROUND(p.skor_hidup, 3) AS skor_hidup "
        "FROM presensi p JOIN mahasiswa m ON m.nim = p.nim "
        "WHERE p.sesi_id = ? ORDER BY p.waktu",
        (sesi_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/sesi/{sesi_id}/presensi", dependencies=[Aman])
def rekap_presensi(sesi_id: int, conn=Depends(get_db)):
    return {"sesi": dict(ambil_sesi(conn, sesi_id)), "hadir": _rekap(conn, sesi_id)}


@app.get("/sesi/{sesi_id}/presensi.csv", dependencies=[Aman])
def rekap_presensi_csv(sesi_id: int, conn=Depends(get_db)):
    ambil_sesi(conn, sesi_id)
    buf = io.StringIO()
    tulis = csv.DictWriter(buf, fieldnames=["nim", "nama", "prodi", "waktu", "skor", "skor_hidup"])
    tulis.writeheader()
    tulis.writerows(_rekap(conn, sesi_id))
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=presensi_sesi_{sesi_id}.csv"},
    )
