"""Penyimpanan SQLite: mahasiswa, wajah, pengguna, kelas/KRS, sesi, dan presensi."""

import sqlite3
from pathlib import Path

import numpy as np

SKEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS mahasiswa (
    nim         TEXT PRIMARY KEY,
    nama        TEXT NOT NULL,
    prodi       TEXT NOT NULL,
    -- NULL = identitas saja (mis. dari impor KRS); wajah belum boleh didaftarkan.
    persetujuan_pada TEXT,
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS wajah (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nim         TEXT NOT NULL REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS pengguna (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nama        TEXT NOT NULL,
    peran       TEXT NOT NULL CHECK (peran IN ('admin', 'dosen')),
    hash_sandi  TEXT NOT NULL,
    aktif       INTEGER NOT NULL DEFAULT 1,
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS token_sesi (
    hash        TEXT PRIMARY KEY,
    pengguna_id INTEGER NOT NULL REFERENCES pengguna(id) ON DELETE CASCADE,
    kedaluwarsa REAL NOT NULL
);
-- Kelas perkuliahan (MK + kelas + semester) yang diampu satu dosen; pesertanya dari KRS.
CREATE TABLE IF NOT EXISTS kelas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kode_mk     TEXT NOT NULL,
    nama_mk     TEXT NOT NULL,
    nama_kelas  TEXT NOT NULL,
    semester    TEXT NOT NULL,
    dosen_id    INTEGER NOT NULL REFERENCES pengguna(id),
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (kode_mk, nama_kelas, semester)
);
CREATE TABLE IF NOT EXISTS kelas_peserta (
    kelas_id    INTEGER NOT NULL REFERENCES kelas(id) ON DELETE CASCADE,
    nim         TEXT NOT NULL REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    PRIMARY KEY (kelas_id, nim)
);
CREATE TABLE IF NOT EXISTS sesi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kode_mk     TEXT NOT NULL,
    nama_mk     TEXT NOT NULL,
    ruang       TEXT NOT NULL,
    dosen       TEXT NOT NULL,
    dosen_id    INTEGER REFERENCES pengguna(id),
    kelas_id    INTEGER REFERENCES kelas(id),
    dibuka      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    ditutup     TEXT
);
CREATE TABLE IF NOT EXISTS presensi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesi_id     INTEGER NOT NULL REFERENCES sesi(id) ON DELETE CASCADE,
    nim         TEXT NOT NULL REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    waktu       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    skor        REAL NOT NULL,
    skor_hidup  REAL,
    UNIQUE (sesi_id, nim)
);
-- Wajah yang ditolak sebagai palsu (foto/layar) untuk audit integritas akademik.
CREATE TABLE IF NOT EXISTS percobaan_palsu (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesi_id     INTEGER NOT NULL REFERENCES sesi(id) ON DELETE CASCADE,
    nim_dugaan  TEXT REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    skor_hidup  REAL NOT NULL,
    skor_kemiripan REAL NOT NULL,
    waktu       TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def _migrasi(conn: sqlite3.Connection) -> None:
    kolom = {r["name"] for r in conn.execute("PRAGMA table_info(presensi)")}
    if "skor_hidup" not in kolom:
        conn.execute("ALTER TABLE presensi ADD COLUMN skor_hidup REAL")
    kolom = {r["name"] for r in conn.execute("PRAGMA table_info(sesi)")}
    if "dosen_id" not in kolom:
        # Sesi lama tanpa pemilik hanya terlihat oleh admin.
        conn.execute("ALTER TABLE sesi ADD COLUMN dosen_id INTEGER REFERENCES pengguna(id)")
    if "kelas_id" not in kolom:
        conn.execute("ALTER TABLE sesi ADD COLUMN kelas_id INTEGER REFERENCES kelas(id)")
    wajib = {r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(mahasiswa)")}
    if wajib.get("persetujuan_pada"):
        _longgarkan_persetujuan(conn)
    conn.commit()


def _longgarkan_persetujuan(conn: sqlite3.Connection) -> None:
    """Jadikan mahasiswa.persetujuan_pada boleh NULL (prosedur rebuild tabel SQLite)."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("""
            BEGIN;
            CREATE TABLE mahasiswa_baru (
                nim         TEXT PRIMARY KEY,
                nama        TEXT NOT NULL,
                prodi       TEXT NOT NULL,
                persetujuan_pada TEXT,
                dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            INSERT INTO mahasiswa_baru (nim, nama, prodi, persetujuan_pada, dibuat)
                SELECT nim, nama, prodi, persetujuan_pada, dibuat FROM mahasiswa;
            DROP TABLE mahasiswa;
            ALTER TABLE mahasiswa_baru RENAME TO mahasiswa;
            COMMIT;
        """)
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Migrasi mahasiswa merusak relasi data")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SKEMA)
    _migrasi(conn)
    return conn


def muat_galeri(conn: sqlite3.Connection) -> tuple[np.ndarray, list[str]]:
    rows = conn.execute("SELECT nim, embedding FROM wajah").fetchall()
    if not rows:
        return np.zeros((0, 128), np.float32), []
    galeri = np.stack([np.frombuffer(r["embedding"], np.float32) for r in rows])
    return galeri, [r["nim"] for r in rows]
