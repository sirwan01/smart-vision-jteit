"""Uji dengan model asli (YuNet, SFace, MiniFASNet).

Dilewati bila model belum diunduh (python scripts/unduh_model.py) atau foto uji
belum ada di FOTO_UJI (bawaan data/foto_uji/). Foto uji tidak disimpan di repo:
- orang_a.jpg, orang_b.jpg : dua orang berbeda (pengenalan)
- asli.jpg, palsu.jpg      : wajah langsung vs. foto/layar yang difoto ulang (anti-spoofing)
"""

import os
from pathlib import Path

import cv2
import pytest

from app.config import get_settings
from app.face_engine import FaceEngine, cocokkan

FOLDER = Path(os.getenv("FOTO_UJI", "data/foto_uji"))
DIR_MODEL = get_settings().model_dir


def perlu(*berkas):
    hilang = [b for b in berkas if not (FOLDER / b).exists()]
    hilang += [m for m in ("face_recognition_sface_2021dec.onnx", "minifasnet_v2.onnx")
               if not (DIR_MODEL / m).exists()]
    return pytest.mark.skipif(bool(hilang), reason=f"Belum tersedia: {hilang}")


@pytest.fixture(scope="module")
def mesin():
    return FaceEngine(DIR_MODEL)


def baca(nama):
    return cv2.imread(str(FOLDER / nama))


@perlu("orang_a.jpg", "orang_b.jpg")
def test_model_membedakan_dua_orang(mesin):
    a, b = baca("orang_a.jpg"), baca("orang_b.jpg")
    ea, eb = mesin.analisis(a), mesin.analisis(b)
    assert len(ea) == 1 and len(eb) == 1

    galeri = ea[0].embedding[None, :]
    ea2 = mesin.analisis(cv2.flip(a, 1))[0].embedding
    assert cocokkan(ea2, galeri, ["A"], 0.363)[0] == "A"
    assert cocokkan(eb[0].embedding, galeri, ["A"], 0.363)[0] is None


@perlu("asli.jpg", "palsu.jpg")
def test_anti_spoofing_membedakan_asli_dan_palsu(mesin):
    ambang = get_settings().ambang_hidup
    asli = mesin.analisis(baca("asli.jpg"))
    palsu = mesin.analisis(baca("palsu.jpg"))
    assert len(asli) == 1 and len(palsu) == 1
    assert asli[0].skor_hidup >= ambang
    assert palsu[0].skor_hidup < ambang
