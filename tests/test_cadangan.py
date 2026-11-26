import gzip
import os
import subprocess
import sys
import time
from pathlib import Path

from app import auth, db

SKRIP = Path(__file__).resolve().parent.parent / "scripts" / "cadangkan.py"


def jalankan(*arg, db_path):
    env = {**os.environ, "DB_PATH": str(db_path)}
    return subprocess.run([sys.executable, str(SKRIP), *arg], env=env, capture_output=True, text=True)


def test_cadang_pulih_dan_retensi(tmp_path):
    sumber = tmp_path / "data" / "presensi.db"
    conn = db.connect(sumber)
    conn.execute("INSERT INTO pengguna (username, nama, peran, hash_sandi) VALUES ('admin','A','admin',?)",
                 (auth.hash_sandi("rahasia123"),))
    conn.execute("INSERT INTO mahasiswa (nim, nama, prodi) VALUES ('230210501001','Andi','PTIK')")
    conn.commit()  # koneksi dibiarkan terbuka: cadangan dibuat saat "aplikasi berjalan"

    tujuan = tmp_path / "cadangan"
    tujuan.mkdir()
    lama = tujuan / "presensi-20200101-000000.db.gz"
    lama.write_bytes(gzip.compress(b"x"))
    os.utime(lama, (time.time() - 40 * 86400,) * 2)

    r = jalankan("--tujuan", str(tujuan), "--simpan-hari", "30", db_path=sumber)
    assert r.returncode == 0, r.stderr
    berkas = sorted(tujuan.glob("presensi-*.db.gz"))
    assert len(berkas) == 1 and not lama.exists()
    assert oct(berkas[0].stat().st_mode & 0o777) == "0o600"

    sasaran = tmp_path / "server-baru" / "presensi.db"  # folder belum ada
    r = jalankan("--pulihkan", str(berkas[0]), db_path=sasaran)
    assert r.returncode == 0, r.stderr
    pulih = db.connect(sasaran)
    assert pulih.execute("SELECT nama FROM mahasiswa").fetchone()[0] == "Andi"
    assert auth.cocok_sandi("rahasia123", pulih.execute("SELECT hash_sandi FROM pengguna").fetchone()[0])
    conn.close()


def test_pulihkan_berkas_rusak_ditolak(tmp_path):
    rusak = tmp_path / "rusak.db.gz"
    rusak.write_bytes(gzip.compress(b"bukan sqlite"))
    sasaran = tmp_path / "x" / "presensi.db"
    r = jalankan("--pulihkan", str(rusak), db_path=sasaran)
    assert r.returncode == 1 and "bukan basis data" in r.stderr
    assert not sasaran.exists()
    bukan_gzip = tmp_path / "b.db.gz"
    bukan_gzip.write_text("teks")
    r = jalankan("--pulihkan", str(bukan_gzip), db_path=sasaran)
    assert r.returncode == 1 and "gzip" in r.stderr
