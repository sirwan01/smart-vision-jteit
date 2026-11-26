import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.face_engine import WajahTerdeteksi


def vektor(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


class MesinTiruan:
    """Pengganti FaceEngine: mengembalikan embedding yang diatur oleh tes."""

    def __init__(self):
        self.wajah: list[np.ndarray] = []

    def analisis(self, gambar):
        return [WajahTerdeteksi((0, 0, 10, 10), 0.99, e) for e in self.wajah]


def jpg() -> bytes:
    return cv2.imencode(".jpg", np.zeros((64, 64, 3), np.uint8))[1].tobytes()


@pytest.fixture
def mesin():
    return MesinTiruan()


@pytest.fixture
def klien(tmp_path, monkeypatch, mesin):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "uji.db"))
    monkeypatch.delenv("API_KEY", raising=False)
    main.settings.cache_clear()
    main.app.dependency_overrides[main.get_engine] = lambda: mesin
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    main.settings.cache_clear()
