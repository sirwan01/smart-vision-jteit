"""Kelola akun dari baris perintah (terutama untuk membuat admin pertama).

Contoh:
    python scripts/kelola_pengguna.py tambah admin --nama "Admin JTETI" --peran admin
    python scripts/kelola_pengguna.py reset-sandi dosen.andi
    python scripts/kelola_pengguna.py nonaktifkan dosen.andi
    python scripts/kelola_pengguna.py daftar

Kata sandi diminta secara interaktif (tidak tampil di layar). Untuk otomasi,
isi variabel lingkungan SANDI_BARU.
"""

import argparse
import getpass
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import auth, db  # noqa: E402
from app.config import get_settings  # noqa: E402


def minta_sandi() -> str:
    if os.getenv("SANDI_BARU"):
        sandi = os.environ["SANDI_BARU"]
    else:
        sandi = getpass.getpass("Kata sandi baru: ")
        if getpass.getpass("Ulangi kata sandi: ") != sandi:
            sys.exit("Kata sandi tidak sama")
    if len(sandi) < auth.PANJANG_SANDI_MIN:
        sys.exit(f"Kata sandi minimal {auth.PANJANG_SANDI_MIN} karakter")
    return sandi


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="perintah", required=True)
    t = sub.add_parser("tambah", help="buat akun baru")
    t.add_argument("username")
    t.add_argument("--nama", required=True)
    t.add_argument("--peran", choices=["admin", "dosen"], default="dosen")
    for nama in ("reset-sandi", "nonaktifkan", "aktifkan"):
        sub.add_parser(nama).add_argument("username")
    sub.add_parser("daftar", help="tampilkan semua akun")
    a = ap.parse_args()

    conn = db.connect(get_settings().db_path)
    if a.perintah == "tambah":
        try:
            conn.execute(
                "INSERT INTO pengguna (username, nama, peran, hash_sandi) VALUES (?, ?, ?, ?)",
                (a.username, a.nama, a.peran, auth.hash_sandi(minta_sandi())),
            )
        except sqlite3.IntegrityError:
            sys.exit(f"Nama pengguna {a.username} sudah ada")
        print(f"Akun {a.username} ({a.peran}) dibuat")
    elif a.perintah == "daftar":
        for r in conn.execute("SELECT username, nama, peran, aktif FROM pengguna ORDER BY peran, nama"):
            print(f"{r['username']:20s} {r['peran']:6s} {'aktif' if r['aktif'] else 'NONAKTIF':9s} {r['nama']}")
    else:
        row = conn.execute("SELECT id FROM pengguna WHERE username = ?", (a.username,)).fetchone()
        if row is None:
            sys.exit(f"Nama pengguna {a.username} tidak ditemukan")
        if a.perintah == "reset-sandi":
            conn.execute("UPDATE pengguna SET hash_sandi = ? WHERE id = ?",
                         (auth.hash_sandi(minta_sandi()), row["id"]))
        else:
            conn.execute("UPDATE pengguna SET aktif = ? WHERE id = ?",
                         (int(a.perintah == "aktifkan"), row["id"]))
        conn.execute("DELETE FROM token_sesi WHERE pengguna_id = ?", (row["id"],))
        print(f"{a.username}: {a.perintah} selesai")
    conn.commit()


if __name__ == "__main__":
    main()
