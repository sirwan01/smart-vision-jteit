import sqlite3

from app import db, main
from tests.conftest import jpg, vektor

KELAS = {"kode_mk": "tik301", "nama_mk": "Visi Komputer", "nama_kelas": "a", "semester": "2026/2027 Ganjil"}


def foto():
    return {"berkas": ("f.jpg", jpg(), "image/jpeg")}


def csv_(isi: str, nama="krs.csv"):
    return {"berkas": (nama, isi.encode("utf-8-sig"), "text/csv")}


def kelas(c, **ubah):
    r = c.post("/kelas", json={**KELAS, **ubah})
    assert r.status_code == 201, r.text
    return r.json()


def test_buat_kelas_dan_kepemilikan(masuk_sebagai):
    admin = masuk_sebagai("admin", "admin")
    andi, budi = masuk_sebagai("dosen.andi", nama="Dr. Andi"), masuk_sebagai("dosen.budi")
    k = kelas(andi)
    assert (k["kode_mk"], k["nama_kelas"], k["dosen"]) == ("TIK301", "A", "Dr. Andi")
    assert andi.post("/kelas", json=KELAS).status_code == 409
    assert [x["id"] for x in andi.get("/kelas").json()] == [k["id"]]
    assert budi.get("/kelas").json() == []
    assert budi.get(f"/kelas/{k['id']}/peserta").status_code == 404
    assert budi.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("nim\n12345\n")).status_code == 404
    # hanya admin yang boleh menetapkan dosen lain
    budi_id = next(u["id"] for u in admin.get("/pengguna").json() if u["username"] == "dosen.budi")
    assert andi.post("/kelas", json={**KELAS, "nama_kelas": "B", "dosen_id": budi_id}).status_code == 403
    kb = admin.post("/kelas", json={**KELAS, "nama_kelas": "B", "dosen_id": budi_id}).json()
    assert [x["id"] for x in budi.get("/kelas").json()] == [kb["id"]]
    assert len(admin.get("/kelas").json()) == 2


def test_impor_krs(klien):
    k = kelas(klien)
    klien.post("/mahasiswa", json={"nim": "230210501001", "nama": "Andi", "prodi": "PTIK", "persetujuan": True})
    isi = (
        "NIM;Nama Mahasiswa;Program Studi\n"      # titik koma (Excel Indonesia), judul campur huruf
        "230210501001;Nama Lain;PTIK\n"           # sudah terdaftar -> hanya ditautkan, nama tidak diubah
        "230210501002;Budi Santoso;PTE\n"         # baru
        "230210501002;Budi Santoso;PTE\n"         # duplikat
        "12;Pendek;PTE\n"                         # NIM tidak valid
        "230210501003;;PTE\n"                     # baru tanpa nama
        ";;\n"                                    # baris kosong diabaikan
    )
    r = klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_(isi)).json()
    assert (r["ditambahkan"], r["sudah_ada"], r["mahasiswa_baru"]) == (2, 1, 1)
    assert [(x["baris"], x["alasan"]) for x in r["ditolak"]] == [
        (5, "NIM tidak valid"), (6, "Mahasiswa baru memerlukan kolom nama")]

    peserta = {p["nim"]: p for p in klien.get(f"/kelas/{k['id']}/peserta").json()}
    assert set(peserta) == {"230210501001", "230210501002"}
    assert peserta["230210501001"]["nama"] == "Andi" and peserta["230210501001"]["persetujuan"] is True
    assert peserta["230210501002"]["persetujuan"] is False
    assert klien.get("/kelas").json()[0]["jumlah_peserta"] == 2

    # impor ulang (koma) idempoten
    r = klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("nim,nama\n230210501002,Budi\n")).json()
    assert (r["ditambahkan"], r["sudah_ada"]) == (0, 1)


def test_impor_csv_tidak_valid(klien):
    k = kelas(klien)
    assert klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("nama,prodi\nA,B\n")).status_code == 422
    assert klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("")).status_code == 422
    besar = "nim,nama\n" + "230210501001,X\n" * 80_000
    assert klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_(besar)).status_code == 413
    latin = {"berkas": ("k.csv", "nim,nama\n230210509999,Andi Muh. Ané\n".encode("latin-1"), "text/csv")}
    assert klien.post(f"/kelas/{k['id']}/peserta/impor", files=latin).json()["mahasiswa_baru"] == 1


def test_persetujuan_dari_krs(klien, mesin):
    k = kelas(klien)
    klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("nim,nama\n230210501002,Budi\n"))
    mesin.wajah = [vektor(2)]
    r = klien.post("/mahasiswa/230210501002/wajah", files=foto())
    assert r.status_code == 403 and "persetujuan" in r.json()["detail"]
    assert klien.post("/mahasiswa/230210501002/persetujuan").json()["persetujuan"] is True
    assert klien.post("/mahasiswa/230210501002/wajah", files=foto()).status_code == 201

    # pencabutan: wajah dihapus, identitas & keanggotaan kelas tetap
    r = klien.delete("/mahasiswa/230210501002/persetujuan").json()
    assert r == {"nim": "230210501002", "persetujuan": False, "wajah_dihapus": 1}
    m = next(x for x in klien.get("/mahasiswa").json() if x["nim"] == "230210501002")
    assert m["jumlah_wajah"] == 0 and m["persetujuan"] is False
    assert len(klien.get(f"/kelas/{k['id']}/peserta").json()) == 1
    assert klien.post("/mahasiswa/999999/persetujuan").status_code == 404


def test_sesi_berkelas_belum_hadir_dan_rekap(masuk_sebagai, mesin):
    andi, budi = masuk_sebagai("dosen.andi"), masuk_sebagai("dosen.budi")
    k = kelas(andi)
    andi.post(f"/kelas/{k['id']}/peserta/impor",
              files=csv_("nim,nama\n230210501001,Andi\n230210501002,Budi\n230210501003,Citra\n"))
    for i, nim in enumerate(["230210501001", "230210501002"], start=1):
        andi.post(f"/mahasiswa/{nim}/persetujuan")
        mesin.wajah = [vektor(i)]
        andi.post(f"/mahasiswa/{nim}/wajah", files=foto())
    # Dedi hadir tetapi tidak ada di KRS kelas ini
    andi.post("/mahasiswa", json={"nim": "230210501099", "nama": "Dedi", "prodi": "PTE", "persetujuan": True})
    mesin.wajah = [vektor(9)]
    andi.post("/mahasiswa/230210501099/wajah", files=foto())

    assert budi.post("/sesi", json={"ruang": "X", "kelas_id": k["id"]}).status_code == 404
    assert andi.post("/sesi", json={"ruang": "X"}).status_code == 422

    # 4 sesi: Andi hadir 4x, Budi 3x (75%), Citra 0x, Dedi 1x (luar KRS)
    for i in range(4):
        s = andi.post("/sesi", json={"ruang": "GD-301", "kelas_id": k["id"]}).json()
        assert (s["kode_mk"], s["kelas_id"]) == ("TIK301", k["id"])
        mesin.wajah = [vektor(1)] + ([vektor(2)] if i < 3 else []) + ([vektor(9)] if i == 0 else [])
        andi.post(f"/sesi/{s['id']}/presensi", files=foto())
        if i == 3:
            d = andi.get(f"/sesi/{s['id']}/presensi").json()
            assert [h["nim"] for h in d["hadir"]] == ["230210501001"]
            assert [b["nim"] for b in d["belum_hadir"]] == ["230210501002", "230210501003"]
            assert d["belum_hadir"][1]["jumlah_wajah"] == 0
        andi.post(f"/sesi/{s['id']}/tutup")

    daftar = andi.get("/sesi").json()
    assert daftar[0]["nama_kelas"] == "A" and daftar[0]["jumlah_peserta"] == 3

    r = andi.get(f"/kelas/{k['id']}/rekap").json()
    assert r["jumlah_sesi"] == 4
    p = {x["nim"]: (x["hadir"], x["persen"], x["memenuhi"]) for x in r["peserta"]}
    assert p == {"230210501001": (4, 100.0, True), "230210501002": (3, 75.0, True),
                 "230210501003": (0, 0.0, False)}
    assert r["hadir_di_luar_krs"] == [{"nim": "230210501099", "nama": "Dedi", "hadir": 1}]
    assert budi.get(f"/kelas/{k['id']}/rekap").status_code == 404

    # mengeluarkan peserta
    assert andi.delete(f"/kelas/{k['id']}/peserta/230210501003").status_code == 200
    assert andi.delete(f"/kelas/{k['id']}/peserta/230210501003").status_code == 404


def test_rekap_kelas_tanpa_sesi(klien):
    k = kelas(klien)
    klien.post(f"/kelas/{k['id']}/peserta/impor", files=csv_("nim,nama\n230210501001,Andi\n"))
    r = klien.get(f"/kelas/{k['id']}/rekap").json()
    assert r["jumlah_sesi"] == 0 and r["peserta"][0]["memenuhi"] is False


def test_migrasi_persetujuan_boleh_kosong(tmp_path):
    p = tmp_path / "lama.db"
    lama = sqlite3.connect(p)
    lama.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE mahasiswa (nim TEXT PRIMARY KEY, nama TEXT NOT NULL, prodi TEXT NOT NULL,
            persetujuan_pada TEXT NOT NULL, dibuat TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE wajah (id INTEGER PRIMARY KEY, nim TEXT NOT NULL REFERENCES mahasiswa(nim)
            ON DELETE CASCADE, embedding BLOB NOT NULL, dibuat TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO mahasiswa (nim, nama, prodi, persetujuan_pada) VALUES ('230210501001', 'Andi', 'PTIK', '2026-11-01');
        INSERT INTO wajah (nim, embedding) VALUES ('230210501001', x'00');
    """)
    lama.close()
    conn = db.connect(p)
    info = {r["name"]: r["notnull"] for r in conn.execute("PRAGMA table_info(mahasiswa)")}
    assert info["persetujuan_pada"] == 0
    assert conn.execute("SELECT nama, persetujuan_pada FROM mahasiswa").fetchone()[:] == ("Andi", "2026-11-01")
    # relasi wajah -> mahasiswa tetap berlaku (cascade) setelah rebuild
    conn.execute("DELETE FROM mahasiswa WHERE nim = '230210501001'")
    assert conn.execute("SELECT COUNT(*) FROM wajah").fetchone()[0] == 0
    conn.close()
    db.connect(p).close()  # migrasi kedua tidak berbuat apa-apa
