import pytest

from app import auth, main
from tests.conftest import SANDI, buat_akun, jpg, vektor

MHS = {"nim": "230210501001", "nama": "Andi", "prodi": "PTIK", "persetujuan": True}


def foto():
    return {"berkas": ("f.jpg", jpg(), "image/jpeg")}


def sesi(c, kode="TIK301"):
    r = c.post("/sesi", json={"kode_mk": kode, "nama_mk": "Visi Komputer", "ruang": "GD-301"})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- kata sandi & login ----------

def test_hash_sandi():
    h = auth.hash_sandi("rahasia123")
    assert h.startswith("scrypt$") and "rahasia123" not in h
    assert auth.cocok_sandi("rahasia123", h)
    assert not auth.cocok_sandi("rahasia124", h)
    assert auth.hash_sandi("rahasia123") != h  # garam acak
    assert not auth.cocok_sandi("x", "rusak")


def test_dasbor_terbuka_tetapi_data_wajib_login(anonim):
    assert anonim.get("/", follow_redirects=True).status_code == 200
    assert anonim.get("/kesehatan").json() == {"status": "ok", "pendaftaran_wajib_aktif": False}
    for path in ("/sesi", "/ringkasan", "/rekap", "/mahasiswa", "/auth/saya", "/pengguna"):
        assert anonim.get(path).status_code == 401, path


def test_login_cookie_dan_keluar(anonim):
    buat_akun("dosen.andi", nama="Dr. Andi")
    assert anonim.post("/auth/masuk", json={"username": "dosen.andi", "sandi": "salah123"}).status_code == 401
    assert anonim.post("/auth/masuk", json={"username": "tidakada", "sandi": SANDI}).status_code == 401
    r = anonim.post("/auth/masuk", json={"username": "DOSEN.ANDI", "sandi": SANDI})
    assert r.status_code == 200 and r.json()["peran"] == "dosen"
    kuki = r.headers["set-cookie"].lower()
    assert "httponly" in kuki and "samesite=strict" in kuki
    assert anonim.get("/auth/saya").json()["nama"] == "Dr. Andi"
    anonim.post("/auth/keluar")
    assert anonim.get("/auth/saya").status_code == 401


def test_token_disimpan_sebagai_hash(anonim):
    buat_akun("dosen.andi")
    anonim.post("/auth/masuk", json={"username": "dosen.andi", "sandi": SANDI})
    token = anonim.cookies.get(auth.NAMA_COOKIE)
    from app import db
    conn = db.connect(main.settings().db_path)
    simpan = [r["hash"] for r in conn.execute("SELECT hash FROM token_sesi")]
    assert token and token not in simpan and len(simpan) == 1


def test_cookie_secure_bila_https(anonim, monkeypatch):
    buat_akun("dosen.andi")
    r = anonim.post("/auth/masuk", json={"username": "dosen.andi", "sandi": SANDI},
                    headers={"x-forwarded-proto": "https"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_pembatasan_percobaan_gagal(anonim):
    buat_akun("dosen.andi")
    for _ in range(auth.MAKS_GAGAL):
        assert anonim.post("/auth/masuk", json={"username": "dosen.andi", "sandi": "salah123"}).status_code == 401
    # bahkan sandi benar ditolak selama masa kunci
    assert anonim.post("/auth/masuk", json={"username": "dosen.andi", "sandi": SANDI}).status_code == 429


def test_ganti_sandi_mengeluarkan_sesi_lain(masuk_sebagai):
    hp = masuk_sebagai("dosen.andi")
    from fastapi.testclient import TestClient
    laptop = TestClient(main.app)
    laptop.post("/auth/masuk", json={"username": "dosen.andi", "sandi": SANDI})
    assert hp.post("/auth/ganti-sandi", json={"sandi_lama": "salah", "sandi_baru": "barubaru1"}).status_code == 403
    assert hp.post("/auth/ganti-sandi", json={"sandi_lama": SANDI, "sandi_baru": "pendek"}).status_code == 422
    assert hp.post("/auth/ganti-sandi", json={"sandi_lama": SANDI, "sandi_baru": "barubaru1"}).status_code == 200
    assert hp.get("/auth/saya").status_code == 200       # sesi ini tetap
    assert laptop.get("/auth/saya").status_code == 401   # sesi lain keluar
    assert laptop.post("/auth/masuk", json={"username": "dosen.andi", "sandi": "barubaru1"}).status_code == 200


# ---------- admin mengelola akun ----------

def test_admin_mengelola_dosen(klien, anonim):
    r = klien.post("/pengguna", json={"username": "dosen.budi", "nama": "Dr. Budi", "sandi": "awal12345"})
    assert r.status_code == 201 and r.json()["peran"] == "dosen"
    assert klien.post("/pengguna", json={"username": "dosen.budi", "nama": "X", "sandi": "awal12345"}).status_code == 409
    assert klien.post("/pengguna", json={"username": "a b", "nama": "X", "sandi": "awal12345"}).status_code == 422
    assert klien.post("/pengguna", json={"username": "dosen.c", "nama": "X", "sandi": "pendek"}).status_code == 422
    uid = r.json()["id"]

    assert anonim.post("/auth/masuk", json={"username": "dosen.budi", "sandi": "awal12345"}).status_code == 200
    # nonaktifkan -> sesi login langsung berakhir dan tidak bisa masuk lagi
    assert klien.patch(f"/pengguna/{uid}", json={"aktif": False}).json()["aktif"] is False
    assert anonim.get("/auth/saya").status_code == 401
    assert anonim.post("/auth/masuk", json={"username": "dosen.budi", "sandi": "awal12345"}).status_code == 401
    # aktifkan + reset sandi
    klien.patch(f"/pengguna/{uid}", json={"aktif": True, "sandi": "reset12345"})
    assert anonim.post("/auth/masuk", json={"username": "dosen.budi", "sandi": "reset12345"}).status_code == 200
    daftar = {u["username"]: u for u in klien.get("/pengguna").json()}
    assert set(daftar) == {"admin", "dosen.budi"} and "hash_sandi" not in daftar["admin"]


def test_admin_tidak_bisa_mengunci_diri(klien):
    saya = klien.get("/auth/saya").json()["id"]
    assert klien.patch(f"/pengguna/{saya}", json={"aktif": False}).status_code == 409
    assert klien.patch(f"/pengguna/{saya}", json={"peran": "dosen"}).status_code == 409
    assert klien.patch("/pengguna/999", json={"nama": "X"}).status_code == 404


def test_dosen_tidak_bisa_fungsi_admin(masuk_sebagai):
    dosen = masuk_sebagai("dosen.andi")
    assert dosen.get("/pengguna").status_code == 403
    assert dosen.post("/pengguna", json={"username": "x", "nama": "x", "sandi": "12345678"}).status_code == 403
    dosen.post("/mahasiswa", json=MHS)
    assert dosen.delete(f"/mahasiswa/{MHS['nim']}").status_code == 403


# ---------- pemisahan data antar-dosen ----------

def test_dosen_hanya_melihat_sesi_miliknya(masuk_sebagai, mesin):
    admin = masuk_sebagai("admin", "admin")
    andi, budi = masuk_sebagai("dosen.andi", nama="Dr. Andi"), masuk_sebagai("dosen.budi", nama="Dr. Budi")
    andi.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    andi.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())

    sa = sesi(andi)
    assert sa["dosen"] == "Dr. Andi"  # nama dosen dari akun, bukan isian bebas
    sb = sesi(budi)
    andi.post(f"/sesi/{sa['id']}/presensi", files=foto())

    assert [s["id"] for s in andi.get("/sesi").json()] == [sa["id"]]
    assert [s["id"] for s in budi.get("/sesi").json()] == [sb["id"]]
    assert {s["id"] for s in admin.get("/sesi").json()} == {sa["id"], sb["id"]}

    # Budi tidak bisa membaca/menutup/mengirim ke sesi Andi (404, tidak bocor)
    for r in (budi.get(f"/sesi/{sa['id']}/presensi"), budi.get(f"/sesi/{sa['id']}/presensi.csv"),
              budi.get(f"/sesi/{sa['id']}/percobaan-palsu"), budi.post(f"/sesi/{sa['id']}/tutup"),
              budi.post(f"/sesi/{sa['id']}/presensi", files=foto())):
        assert r.status_code == 404
    assert admin.get(f"/sesi/{sa['id']}/presensi").json()["hadir"][0]["nim"] == MHS["nim"]

    assert andi.get("/ringkasan").json()["hadir_hari_ini"] == 1
    assert budi.get("/ringkasan").json()["hadir_hari_ini"] == 0
    assert admin.get("/ringkasan").json()["sesi_aktif"] == 2


def test_rekap_kelas_paralel_dipisah_per_dosen(masuk_sebagai, mesin):
    admin = masuk_sebagai("admin", "admin")
    andi, budi = masuk_sebagai("dosen.andi"), masuk_sebagai("dosen.budi")
    andi.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    andi.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    # MK sama, kelas paralel: Andi 2 sesi (mhs hadir 2x), Budi 1 sesi
    for _ in range(2):
        s = sesi(andi)
        andi.post(f"/sesi/{s['id']}/presensi", files=foto())
    sesi(budi)

    assert andi.get("/rekap").json()[0]["jumlah_sesi"] == 2
    assert budi.get("/rekap").json()[0]["jumlah_sesi"] == 1
    assert andi.get("/rekap/TIK301").json()["peserta"][0]["persen"] == 100.0
    assert budi.get("/rekap/TIK301").json()["peserta"] == []
    assert admin.get("/rekap/TIK301").json()["jumlah_sesi"] == 3


# ---------- kunci perangkat kamera ----------

def test_kunci_perangkat_hanya_untuk_kirim_bingkai(masuk_sebagai, anonim, monkeypatch, mesin):
    monkeypatch.setenv("API_KEY", "kunci-kamera")
    main.settings.cache_clear()
    andi = masuk_sebagai("dosen.andi")
    andi.post("/mahasiswa", json=MHS)
    mesin.wajah = [vektor(1)]
    andi.post(f"/mahasiswa/{MHS['nim']}/wajah", files=foto())
    s = sesi(andi)

    kunci = {"X-API-Key": "kunci-kamera"}
    r = anonim.post(f"/sesi/{s['id']}/presensi", files=foto(), headers=kunci)
    assert r.status_code == 200 and r.json()["dikenali"][0]["nim"] == MHS["nim"]
    assert anonim.post(f"/sesi/{s['id']}/presensi", files=foto(),
                       headers={"X-API-Key": "salah"}).status_code == 401
    # kunci perangkat tidak bisa membaca data
    for path in ("/sesi", f"/sesi/{s['id']}/presensi", "/mahasiswa", "/rekap"):
        assert anonim.get(path, headers=kunci).status_code == 401, path


def test_tanpa_api_key_perangkat_tidak_bisa_mengirim(masuk_sebagai, anonim):
    andi = masuk_sebagai("dosen.andi")
    s = sesi(andi)
    assert anonim.post(f"/sesi/{s['id']}/presensi", files=foto(),
                       headers={"X-API-Key": ""}).status_code == 401
