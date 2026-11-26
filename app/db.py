"""Penyimpanan SQLite untuk mahasiswa, embedding wajah, sesi, dan presensi."""

import sqlite3
from pathlib import Path

import numpy as np

SKEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS mahasiswa (
    nim         TEXT PRIMARY KEY,
    nama        TEXT NOT NULL,
    prodi       TEXT NOT NULL,
    persetujuan_pada TEXT NOT NULL,
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS wajah (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nim         TEXT NOT NULL REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    embedding   BLOB NOT NULL,
    dibuat      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS sesi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kode_mk     TEXT NOT NULL,
    nama_mk     TEXT NOT NULL,
    ruang       TEXT NOT NULL,
    dosen       TEXT NOT NULL,
    dibuka      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    ditutup     TEXT
);
CREATE TABLE IF NOT EXISTS presensi (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesi_id     INTEGER NOT NULL REFERENCES sesi(id) ON DELETE CASCADE,
    nim         TEXT NOT NULL REFERENCES mahasiswa(nim) ON DELETE CASCADE,
    waktu       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    skor        REAL NOT NULL,
    UNIQUE (sesi_id, nim)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SKEMA)
    return conn


def muat_galeri(conn: sqlite3.Connection) -> tuple[np.ndarray, list[str]]:
    rows = conn.execute("SELECT nim, embedding FROM wajah").fetchall()
    if not rows:
        return np.zeros((0, 128), np.float32), []
    galeri = np.stack([np.frombuffer(r["embedding"], np.float32) for r in rows])
    return galeri, [r["nim"] for r in rows]
