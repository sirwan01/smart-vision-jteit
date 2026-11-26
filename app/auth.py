"""Autentikasi pengguna (admin jurusan, dosen) dan kunci perangkat kamera.

- Kata sandi di-hash dengan scrypt (pustaka standar), garam acak per pengguna.
- Login menghasilkan token acak; yang disimpan di basis data hanya SHA-256-nya.
  Token dikirim sebagai cookie HttpOnly + SameSite=Strict (tidak terbaca JavaScript,
  tidak terkirim dari situs lain).
- Percobaan login gagal dibatasi per nama pengguna.
- `API_KEY` (opsional) adalah kunci perangkat kamera kelas: hanya boleh mengirim
  bingkai presensi, tidak dapat membaca data apa pun.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

NAMA_COOKIE = "sesi_presensi"
UMUR_SESI_DETIK = 12 * 3600
MAKS_GAGAL = 5
JENDELA_GAGAL_DETIK = 15 * 60
PANJANG_SANDI_MIN = 8

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


# ---------- kata sandi ----------

def hash_sandi(sandi: str) -> str:
    garam = secrets.token_bytes(16)
    h = hashlib.scrypt(sandi.encode(), salt=garam, n=_N, r=_R, p=_P, dklen=_DKLEN)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(garam)}${b64(h)}"


def cocok_sandi(sandi: str, tersimpan: str) -> bool:
    try:
        _, n, r, p, garam, h = tersimpan.split("$")
        hitung = hashlib.scrypt(
            sandi.encode(), salt=base64.b64decode(garam),
            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(h)),
        )
        return hmac.compare_digest(hitung, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# Hash tiruan agar waktu respons sama ketika nama pengguna tidak ada.
_HASH_TIRUAN = hash_sandi(secrets.token_hex(8))


def periksa_kekuatan(sandi: str) -> None:
    if len(sandi) < PANJANG_SANDI_MIN:
        raise HTTPException(422, f"Kata sandi minimal {PANJANG_SANDI_MIN} karakter")


# ---------- pembatasan percobaan gagal ----------

class PembatasLogin:
    def __init__(self):
        self._gagal: dict[str, list[float]] = {}
        self._kunci = threading.Lock()

    def _bersihkan(self, kunci: str, kini: float) -> list[float]:
        daftar = [t for t in self._gagal.get(kunci, []) if kini - t < JENDELA_GAGAL_DETIK]
        self._gagal[kunci] = daftar
        return daftar

    def cek(self, username: str) -> None:
        with self._kunci:
            if len(self._bersihkan(username.lower(), time.time())) >= MAKS_GAGAL:
                raise HTTPException(
                    429, "Terlalu banyak percobaan gagal. Coba lagi dalam 15 menit."
                )

    def catat_gagal(self, username: str) -> None:
        with self._kunci:
            kini = time.time()
            self._bersihkan(username.lower(), kini).append(kini)

    def reset(self, username: str | None = None) -> None:
        with self._kunci:
            if username is None:
                self._gagal.clear()
            else:
                self._gagal.pop(username.lower(), None)


pembatas = PembatasLogin()


# ---------- token sesi ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def buat_token(conn: sqlite3.Connection, pengguna_id: int) -> str:
    token = secrets.token_urlsafe(32)
    kini = time.time()
    conn.execute("DELETE FROM token_sesi WHERE kedaluwarsa < ?", (kini,))
    conn.execute(
        "INSERT INTO token_sesi (hash, pengguna_id, kedaluwarsa) VALUES (?, ?, ?)",
        (_hash_token(token), pengguna_id, kini + UMUR_SESI_DETIK),
    )
    conn.commit()
    return token


def hapus_token(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM token_sesi WHERE hash = ?", (_hash_token(token),))
    conn.commit()


@dataclass(frozen=True)
class Pengguna:
    id: int
    username: str
    nama: str
    peran: str  # "admin" | "dosen"

    @property
    def admin(self) -> bool:
        return self.peran == "admin"

    def publik(self) -> dict:
        return {"id": self.id, "username": self.username, "nama": self.nama, "peran": self.peran}


def pengguna_dari_token(conn: sqlite3.Connection, token: str | None) -> Pengguna | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT p.id, p.username, p.nama, p.peran FROM token_sesi t "
        "JOIN pengguna p ON p.id = t.pengguna_id "
        "WHERE t.hash = ? AND t.kedaluwarsa > ? AND p.aktif = 1",
        (_hash_token(token), time.time()),
    ).fetchone()
    return Pengguna(**dict(row)) if row else None


def masuk(conn: sqlite3.Connection, username: str, sandi: str) -> Pengguna:
    pembatas.cek(username)
    row = conn.execute(
        "SELECT id, username, nama, peran, hash_sandi, aktif FROM pengguna WHERE username = ?",
        (username,),
    ).fetchone()
    sah = cocok_sandi(sandi, row["hash_sandi"] if row else _HASH_TIRUAN)
    if not (row and sah and row["aktif"]):
        pembatas.catat_gagal(username)
        raise HTTPException(401, "Nama pengguna atau kata sandi salah")
    pembatas.reset(username)
    return Pengguna(row["id"], row["username"], row["nama"], row["peran"])


def cookie_aman(request: Request, paksa: bool | None) -> bool:
    if paksa is not None:
        return paksa
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
