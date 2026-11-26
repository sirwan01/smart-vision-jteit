import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import auth, db, main
from app.face_engine import WajahTerdeteksi


def vektor(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


class MesinTiruan:
    """Pengganti FaceEngine: mengembalikan embedding yang diatur oleh tes."""

    def __init__(self):
        # Tiap entri: embedding, atau (embedding, skor_hidup). Bawaan skor_hidup = 0.99.
        self.wajah: list = []

    def analisis(self, gambar):
        hasil = []
        for e in self.wajah:
            if isinstance(e, dict):  # {"emb", "hidup", "yaw"}
                emb, hidup, yaw = e["emb"], e.get("hidup", 0.99), e.get("yaw", 0.0)
            else:
                (emb, hidup), yaw = (e if isinstance(e, tuple) else (e, 0.99)), 0.0
            hasil.append(WajahTerdeteksi((0, 0, 10, 10), 0.99, emb, hidup, titik_wajah(yaw)))
        return hasil


def titik_wajah(yaw: float) -> np.ndarray:
    """5 titik wajah buatan (jarak antarmata 20 px) dengan rasio yaw tertentu."""
    return np.array([[40, 50], [60, 50], [50 + 20 * yaw, 60], [42, 70], [58, 70]], np.float32)


def jpg() -> bytes:
    return cv2.imencode(".jpg", np.zeros((64, 64, 3), np.uint8))[1].tobytes()


@pytest.fixture
def mesin():
    return MesinTiruan()


SANDI = "sandi-uji-123"


def buat_akun(username, peran="dosen", nama=None, sandi=SANDI):
    conn = db.connect(main.settings().db_path)
    conn.execute(
        "INSERT INTO pengguna (username, nama, peran, hash_sandi) VALUES (?, ?, ?, ?)",
        (username, nama or username.title(), peran, auth.hash_sandi(sandi)),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def lingkungan(tmp_path, monkeypatch, mesin):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "uji.db"))
    # Uji selalu memakai konfigurasi bawaan, apa pun variabel lingkungan mesinnya
    # (mis. devcontainer/produksi menyetel WAJIB_HIDUP_AKTIF_DAFTAR=1).
    for nama in ("API_KEY", "WAJIB_HIDUP_AKTIF_DAFTAR", "COOKIE_AMAN", "ANTI_SPOOFING",
                 "AMBANG_KEMIRIPAN", "AMBANG_HIDUP", "AMBANG_DETEKSI"):
        monkeypatch.delenv(nama, raising=False)
    main.settings.cache_clear()
    auth.pembatas.reset()
    main.app.dependency_overrides[main.get_engine] = lambda: mesin
    yield
    main.app.dependency_overrides.clear()
    main.settings.cache_clear()


@pytest.fixture
def masuk_sebagai(lingkungan):
    """Pabrik klien yang sudah login; tiap klien punya cookie sendiri."""
    klien = []

    def buat(username, peran="dosen", nama=None):
        buat_akun(username, peran, nama)
        c = TestClient(main.app)
        r = c.post("/auth/masuk", json={"username": username, "sandi": SANDI})
        assert r.status_code == 200, r.text
        klien.append(c)
        return c

    yield buat
    for c in klien:
        c.close()


@pytest.fixture
def klien(masuk_sebagai):
    """Klien yang login sebagai admin jurusan."""
    return masuk_sebagai("admin", "admin", "Admin JTEIT")


@pytest.fixture
def anonim(lingkungan):
    with TestClient(main.app) as c:
        yield c
