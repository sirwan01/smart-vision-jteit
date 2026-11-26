import numpy as np

from app import main
from app.face_engine import cocokkan
from tests.conftest import jpg, vektor

MHS = {"nim": "230210501001", "nama": "Andi", "prodi": "PTIK", "persetujuan": True}


def foto():
    return {"berkas": ("f.jpg", jpg(), "image/jpeg")}


def test_cocokkan_ambang():
    a, b = vektor(1), vektor(2)
    galeri = np.stack([a, b])
    assert cocokkan(a, galeri, ["A", "B"], 0.363)[0] == "A"
    assert cocokkan(vektor(3), galeri, ["A", "B"], 0.363)[0] is None
    assert cocokkan(a, np.zeros((0, 128), np.float32), [], 0.363) == (None, 0.0)


def test_pendaftaran_butuh_persetujuan(klien):
    r = klien.post("/mahasiswa", json={**MHS, "persetujuan": False})
    assert r.status_code == 422
    assert klien.post("/mahasiswa", json=MHS).status_code == 201
    assert klien.post("/mahasiswa", json=MHS).status_code == 409


def test_foto_pendaftaran_harus_satu_wajah(klien, mesin):
    klien.post("/mahasiswa", json=MHS)
    mesin.wajah = []
    assert klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto()).status_code == 422
    mesin.wajah = [vektor(1), vektor(2)]
    assert klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto()).status_code == 422
    mesin.wajah = [vektor(1)]
    r = klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    assert r.status_code == 201 and r.json()["jumlah_wajah"] == 1


def test_bukan_gambar_ditolak(klien):
    klien.post("/mahasiswa", json=MHS)
    r = klien.post(
        f"/mahasiswa/{MHS['nim']}/wajah", files={"berkas": ("x.jpg", b"bukan", "image/jpeg")}
    )
    assert r.status_code == 400


def test_alur_presensi_lengkap(klien, mesin):
    klien.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())

    sesi = klien.post(
        "/sesi",
        json={"kode_mk": "TIK101", "nama_mk": "Visi Komputer", "ruang": "GD-301", "dosen": "Dr. X"},
    ).json()

    # satu wajah dikenal (sedikit berderau) + satu orang asing
    derau = vektor(1) + 0.05 * vektor(9)
    mesin.wajah = [derau / np.linalg.norm(derau), vektor(7)]
    r = klien.post(f"/sesi/{sesi['id']}/presensi", files=foto()).json()
    assert r["jumlah_wajah"] == 2
    assert [d["nim"] for d in r["dikenali"]] == [MHS["nim"]]
    assert r["dikenali"][0]["baru_dicatat"] is True
    assert len(r["tidak_dikenali"]) == 1

    # bingkai berikutnya tidak mencatat ganda
    r = klien.post(f"/sesi/{sesi['id']}/presensi", files=foto()).json()
    assert r["dikenali"][0]["baru_dicatat"] is False

    rekap = klien.get(f"/sesi/{sesi['id']}/presensi").json()
    assert [h["nim"] for h in rekap["hadir"]] == [MHS["nim"]]
    csv = klien.get(f"/sesi/{sesi['id']}/presensi.csv").text
    assert csv.splitlines()[0] == "nim,nama,prodi,waktu,skor" and MHS["nim"] in csv

    assert klien.post(f"/sesi/{sesi['id']}/tutup").json()["ditutup"] is not None
    assert klien.post(f"/sesi/{sesi['id']}/presensi", files=foto()).status_code == 409


def test_hapus_mahasiswa_menghapus_wajah_dan_presensi(klien, mesin):
    klien.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    klien.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    sesi = klien.post(
        "/sesi", json={"kode_mk": "A", "nama_mk": "B", "ruang": "C", "dosen": "D"}
    ).json()
    klien.post(f"/sesi/{sesi['id']}/presensi", files=foto())

    assert klien.delete(f"/mahasiswa/{MHS['nim']}").status_code == 200
    assert klien.get("/mahasiswa").json() == []
    assert klien.get(f"/sesi/{sesi['id']}/presensi").json()["hadir"] == []
    assert klien.delete(f"/mahasiswa/{MHS['nim']}").status_code == 404


def test_api_key(klien, monkeypatch):
    monkeypatch.setenv("API_KEY", "rahasia-uji")
    main.settings.cache_clear()
    assert klien.get("/mahasiswa").status_code == 401
    assert klien.get("/mahasiswa", headers={"X-API-Key": "rahasia-uji"}).status_code == 200
    assert klien.get("/kesehatan").status_code == 200
