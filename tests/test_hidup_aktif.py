import random

import numpy as np
import pytest

from app import hidup_aktif as ha
from app import main
from tests.conftest import jpg, titik_wajah, vektor

MHS = {"nim": "230210501001", "nama": "Andi", "prodi": "PTIK", "persetujuan": True}
YAW = {"tengah": 0.0, "kiri": 0.5, "kanan": -0.5}


def foto():
    return {"berkas": ("f.jpg", jpg(), "image/jpeg")}


@pytest.fixture(autouse=True)
def bersih():
    ha.pengelola.reset()
    yield
    ha.pengelola.reset()


def kirim(c, tid, mesin, yaw, emb=None, hidup=0.99, headers=None):
    mesin.wajah = [{"emb": vektor(1) if emb is None else emb, "hidup": hidup, "yaw": yaw}]
    r = c.post(f"/hidup/tantangan/{tid}/bingkai", files=foto(), headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


def jalankan(c, t, mesin, **kw):
    """Ikuti urutan langkah dengan benar; kembalikan respons terakhir."""
    r = None
    for langkah in t["langkah"]:
        r = kirim(c, t["id"], mesin, YAW[langkah], **kw)
    return r


def siapkan(c, mesin, **sesi_kw):
    c.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    c.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    return c.post("/sesi", json={"kode_mk": "TIK301", "nama_mk": "VK", "ruang": "GD", **sesi_kw}).json()


# ---------- logika murni ----------

def test_arah_dan_pose():
    assert ha.arah_kepala(titik_wajah(0.0)) == 0.0
    assert ha.arah_kepala(titik_wajah(0.5)) == pytest.approx(0.5)
    assert ha.pose_dari_yaw(0.05) == "tengah"
    assert ha.pose_dari_yaw(0.25) is None  # zona antara
    assert ha.pose_dari_yaw(0.5) == "kiri" and ha.pose_dari_yaw(-0.5) == "kanan"


def test_urutan_acak():
    u = ha.urutan_acak(random.Random(1))
    assert len(u) == 2 * ha.JUMLAH_TOLEH + 1
    assert u[::2] == ["tengah"] * (ha.JUMLAH_TOLEH + 1)
    assert set(u[1::2]) <= {"kiri", "kanan"}
    semua = {tuple(ha.urutan_acak()) for _ in range(300)}
    assert len(semua) == 2 ** ha.JUMLAH_TOLEH  # 8 kemungkinan urutan


# ---------- presensi mode kios ----------

def test_kios_lulus_mencatat_presensi_aktif(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    assert t["status"] == "berjalan" and t["petunjuk"] == ha.PETUNJUK["tengah"]
    r = jalankan(klien, t, mesin)
    assert r["status"] == "lulus"
    assert r["hasil"] == {"dikenali": True, "nim": MHS["nim"], "nama": "Andi", "skor": 1.0, "baru_dicatat": True}
    hadir = klien.get(f"/sesi/{s['id']}/presensi").json()["hadir"]
    assert hadir[0]["metode"] == "aktif" and hadir[0]["skor_hidup"] == 0.99
    assert "metode" in klien.get(f"/sesi/{s['id']}/presensi.csv").text.splitlines()[0]
    # sekali pakai: bingkai tambahan tidak memproses ulang
    assert "hasil" not in kirim(klien, t["id"], mesin, 0.0)


def test_foto_diam_tidak_pernah_lulus(klien, mesin, monkeypatch):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    for _ in range(20):  # kamera virtual menayangkan gambar diam (selalu menghadap depan)
        r = kirim(klien, t["id"], mesin, 0.0)
    assert r["status"] == "berjalan" and r["posisi"] == 1
    monkeypatch.setattr(ha, "BATAS_DETIK", 0)
    r = kirim(klien, t["id"], mesin, 0.0)
    assert (r["status"], r["alasan"]) == ("gagal", "Waktu habis")
    assert klien.get(f"/sesi/{s['id']}/presensi").json()["hadir"] == []


def test_arah_salah_gagal(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    kirim(klien, t["id"], mesin, 0.0)
    salah = "kanan" if t["langkah"][1] == "kiri" else "kiri"
    r = kirim(klien, t["id"], mesin, YAW[salah])
    assert (r["status"], r["alasan"]) == ("gagal", "Arah toleh tidak sesuai petunjuk")


def test_zona_antara_dan_tanpa_wajah_tidak_menggagalkan(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    kirim(klien, t["id"], mesin, 0.0)
    assert kirim(klien, t["id"], mesin, 0.25)["status"] == "berjalan"  # setengah menoleh
    mesin.wajah = []
    r = klien.post(f"/hidup/tantangan/{t['id']}/bingkai", files=foto()).json()
    assert r["status"] == "berjalan" and r["pesan"] == "Wajah tidak terlihat"
    mesin.wajah = [{"emb": vektor(1), "yaw": 0}, {"emb": vektor(2), "yaw": 0}]
    r = klien.post(f"/hidup/tantangan/{t['id']}/bingkai", files=foto()).json()
    assert "satu wajah" in r["pesan"]


def test_wajah_berganti_gagal(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    kirim(klien, t["id"], mesin, 0.0)  # Andi memulai
    r = kirim(klien, t["id"], mesin, YAW[t["langkah"][1]], emb=vektor(7))  # orang lain menoleh
    assert (r["status"], r["alasan"]) == ("gagal", "Wajah berganti selama verifikasi")


def test_layar_yang_digerakkan_gagal_oleh_anti_spoofing_pasif(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]})
    t = t.json()
    kirim(klien, t["id"], mesin, 0.0, hidup=0.05)
    r = kirim(klien, t["id"], mesin, 0.0, hidup=0.05)
    assert (r["status"], r["alasan"]) == ("gagal", "Terdeteksi foto/layar, bukan wajah langsung")


def test_orang_asing_lulus_tapi_tidak_dicatat(klien, mesin):
    s = siapkan(klien, mesin)
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    r = jalankan(klien, t, mesin, emb=vektor(5))
    assert r["status"] == "lulus" and r["hasil"]["dikenali"] is False
    assert klien.get(f"/sesi/{s['id']}/presensi").json()["hadir"] == []


def test_sesi_wajib_aktif_menolak_kamera_pasif(klien, mesin):
    s = siapkan(klien, mesin, wajib_hidup_aktif=True)
    assert s["wajib_hidup_aktif"] == 1
    mesin.wajah = [vektor(1)]
    r = klien.post(f"/sesi/{s['id']}/presensi", files=foto())
    assert r.status_code == 409 and "mode kios" in r.json()["detail"]
    t = klien.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    assert jalankan(klien, t, mesin)["hasil"]["baru_dicatat"] is True


def test_tantangan_terikat_pemilik_dan_sesi(masuk_sebagai, mesin, monkeypatch):
    andi, budi = masuk_sebagai("dosen.andi"), masuk_sebagai("dosen.budi")
    s = siapkan(andi, mesin)
    assert budi.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).status_code == 404
    t = andi.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).json()
    assert budi.post(f"/hidup/tantangan/{t['id']}/bingkai", files=foto()).status_code == 404
    assert andi.post("/hidup/tantangan", json={"tujuan": "presensi"}).status_code == 422
    andi.post(f"/sesi/{s['id']}/tutup")
    assert andi.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}).status_code == 409


def test_perangkat_kamera_boleh_kios_tapi_tidak_pendaftaran(masuk_sebagai, anonim, mesin, monkeypatch):
    monkeypatch.setenv("API_KEY", "kunci-kamera")
    main.settings.cache_clear()
    andi = masuk_sebagai("dosen.andi")
    s = siapkan(andi, mesin)
    kunci = {"X-API-Key": "kunci-kamera"}
    t = anonim.post("/hidup/tantangan", json={"tujuan": "presensi", "sesi_id": s["id"]}, headers=kunci).json()
    assert jalankan(anonim, t, mesin, headers=kunci)["hasil"]["nim"] == MHS["nim"]
    assert anonim.post("/hidup/tantangan", json={"tujuan": "pendaftaran", "nim": MHS["nim"]},
                       headers=kunci).status_code == 401


# ---------- pendaftaran wajah dengan verifikasi gerak ----------

def test_pendaftaran_lewat_tantangan(klien, mesin, monkeypatch):
    monkeypatch.setenv("WAJIB_HIDUP_AKTIF_DAFTAR", "1")
    main.settings.cache_clear()
    assert klien.get("/kesehatan").json()["pendaftaran_wajib_aktif"] is True
    klien.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    r = klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    assert r.status_code == 403 and "gerak" in r.json()["detail"]

    t = klien.post("/hidup/tantangan", json={"tujuan": "pendaftaran", "nim": MHS["nim"]}).json()
    r = jalankan(klien, t, mesin)
    assert r["status"] == "lulus" and r["hasil"] == {"nim": MHS["nim"], "jumlah_wajah": 1}
    # embedding tersimpan dapat dipakai pengenalan
    from app import db
    galeri, label = db.muat_galeri(db.connect(main.settings().db_path))
    assert label == [MHS["nim"]] and float(galeri[0] @ vektor(1)) == pytest.approx(1.0, abs=1e-5)


def test_pendaftaran_tantangan_butuh_persetujuan(klien):
    klien.post("/kelas", json={"kode_mk": "A", "nama_mk": "B", "nama_kelas": "A", "semester": "S"})
    klien.post("/kelas/1/peserta/impor", files={"berkas": ("k.csv", b"nim,nama\n230210509999,Z\n", "text/csv")})
    r = klien.post("/hidup/tantangan", json={"tujuan": "pendaftaran", "nim": "230210509999"})
    assert r.status_code == 403
    assert klien.post("/hidup/tantangan", json={"tujuan": "pendaftaran", "nim": "000000"}).status_code == 404
