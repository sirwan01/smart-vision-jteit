"""Uji dengan model YuNet + SFace asli.

Dilewati bila model belum diunduh (python scripts/unduh_model.py) atau
FOTO_UJI belum menunjuk folder berisi dua foto wajah orang berbeda:
`orang_a.jpg` dan `orang_b.jpg`. Foto uji tidak disimpan di repo.
"""

import os
from pathlib import Path

import cv2
import pytest

from app.config import get_settings
from app.face_engine import FaceEngine, cocokkan

FOLDER = Path(os.getenv("FOTO_UJI", "data/foto_uji"))
DIR_MODEL = get_settings().model_dir

pytestmark = pytest.mark.skipif(
    not (FOLDER / "orang_a.jpg").exists()
    or not (FOLDER / "orang_b.jpg").exists()
    or not (DIR_MODEL / "face_recognition_sface_2021dec.onnx").exists(),
    reason="Model atau foto uji belum tersedia",
)


def test_model_membedakan_dua_orang():
    mesin = FaceEngine(DIR_MODEL)
    a = cv2.imread(str(FOLDER / "orang_a.jpg"))
    b = cv2.imread(str(FOLDER / "orang_b.jpg"))
    ea, eb = mesin.analisis(a), mesin.analisis(b)
    assert len(ea) == 1 and len(eb) == 1

    galeri = ea[0].embedding[None, :]
    # orang yang sama (foto dicerminkan) dikenali
    ea2 = mesin.analisis(cv2.flip(a, 1))[0].embedding
    assert cocokkan(ea2, galeri, ["A"], 0.363)[0] == "A"
    # orang berbeda ditolak
    assert cocokkan(eb[0].embedding, galeri, ["A"], 0.363)[0] is None
