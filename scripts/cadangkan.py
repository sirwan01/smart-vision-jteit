"""Cadangkan basis data SQLite secara aman saat aplikasi berjalan.

Memakai API backup SQLite (konsisten walau ada penulisan), memeriksa integritas
salinan, mengompresi (gzip), dan menghapus cadangan yang lebih tua dari N hari.

Contoh:
    python scripts/cadangkan.py --tujuan /var/backups/smart-vision-jteit --simpan-hari 30
    python scripts/cadangkan.py --pulihkan /var/backups/.../presensi-20261126-011700.db.gz

Pemulihan menulis ke DB_PATH; HENTIKAN layanan terlebih dahulu.
"""

import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

AWALAN = "presensi-"


def periksa(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        hasil = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if hasil != "ok":
            sys.exit(f"Integritas gagal: {hasil}")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            sys.exit("Pemeriksaan relasi (foreign key) gagal")
    except sqlite3.DatabaseError as e:
        sys.exit(f"Berkas bukan basis data SQLite yang sah: {e}")
    finally:
        conn.close()


def cadangkan(sumber: Path, tujuan: Path, simpan_hari: int) -> Path:
    if not sumber.exists():
        sys.exit(f"Basis data {sumber} tidak ditemukan")
    tujuan.mkdir(parents=True, exist_ok=True)
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    akhir = tujuan / f"{AWALAN}{stempel}.db.gz"
    with tempfile.TemporaryDirectory(dir=tujuan) as tmp:
        salinan = Path(tmp) / "salinan.db"
        src, dst = sqlite3.connect(sumber), sqlite3.connect(salinan)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        periksa(salinan)
        sementara = akhir.with_suffix(".part")
        with open(salinan, "rb") as f_in, gzip.open(sementara, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.chmod(sementara, 0o600)
        sementara.replace(akhir)
    batas = time.time() - simpan_hari * 86400
    for lama in tujuan.glob(f"{AWALAN}*.db.gz"):
        if lama != akhir and lama.stat().st_mtime < batas:
            lama.unlink()
    return akhir


def pulihkan(berkas: Path, sasaran: Path) -> None:
    if not berkas.exists():
        sys.exit(f"Berkas cadangan {berkas} tidak ditemukan")
    sasaran.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=sasaran.parent) as tmp:
        sementara = Path(tmp) / "pulih.db"
        try:
            with gzip.open(berkas, "rb") as f_in, open(sementara, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        except (OSError, EOFError) as e:
            sys.exit(f"Berkas cadangan tidak dapat dibuka (bukan gzip yang sah?): {e}")
        periksa(sementara)
        if sasaran.exists():
            cadangan_lama = sasaran.with_name(sasaran.name + f".sebelum-pulih-{int(time.time())}")
            sasaran.replace(cadangan_lama)
            print(f"Basis data lama disimpan sebagai {cadangan_lama}")
        sementara.replace(sasaran)
        os.chmod(sasaran, 0o600)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tujuan", type=Path, help="folder penyimpanan cadangan")
    ap.add_argument("--simpan-hari", type=int, default=30)
    ap.add_argument("--pulihkan", type=Path, help="berkas .db.gz yang akan dipulihkan ke DB_PATH")
    a = ap.parse_args()
    db_path = get_settings().db_path
    if a.pulihkan:
        pulihkan(a.pulihkan, db_path)
        print(f"Dipulihkan ke {db_path}")
    elif a.tujuan:
        print(f"Cadangan dibuat: {cadangkan(db_path, a.tujuan, a.simpan_hari)}")
    else:
        ap.error("isi --tujuan atau --pulihkan")


if __name__ == "__main__":
    main()
