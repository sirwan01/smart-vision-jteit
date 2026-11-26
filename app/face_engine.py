"""Mesin pengenalan wajah: deteksi YuNet + embedding SFace (OpenCV, CPU)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.liveness import DeteksiHidup

MODEL_DETEKSI = "face_detection_yunet_2023mar.onnx"
MODEL_PENGENAL = "face_recognition_sface_2021dec.onnx"
SISI_MAKS = 1280  # gambar lebih besar diperkecil agar cepat di CPU


@dataclass
class WajahTerdeteksi:
    kotak: tuple[int, int, int, int]  # x, y, lebar, tinggi
    skor_deteksi: float
    embedding: np.ndarray  # vektor 128 dimensi, ternormalisasi L2
    skor_hidup: float | None = None  # probabilitas wajah asli; None bila anti-spoofing nonaktif
    # 5 titik wajah YuNet (mata, mata, hidung, sudut mulut x2), bentuk (5, 2)
    landmark: np.ndarray | None = None


def decode_gambar(data: bytes) -> np.ndarray:
    gambar = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if gambar is None:
        raise ValueError("Berkas bukan gambar yang valid")
    h, w = gambar.shape[:2]
    skala = SISI_MAKS / max(h, w)
    if skala < 1:
        gambar = cv2.resize(gambar, (int(w * skala), int(h * skala)))
    return gambar


class FaceEngine:
    def __init__(self, model_dir: Path, ambang_deteksi: float = 0.8, anti_spoofing: bool = True):
        det = Path(model_dir) / MODEL_DETEKSI
        rec = Path(model_dir) / MODEL_PENGENAL
        for p in (det, rec):
            if not p.exists():
                raise FileNotFoundError(
                    f"Model {p} belum ada. Jalankan: python scripts/unduh_model.py"
                )
        self._detektor = cv2.FaceDetectorYN.create(
            str(det), "", (320, 320), ambang_deteksi, 0.3, 5000
        )
        self._pengenal = cv2.FaceRecognizerSF.create(str(rec), "")
        self._hidup = DeteksiHidup(model_dir) if anti_spoofing else None
        # Objek OpenCV tidak aman dipakai paralel; FastAPI menjalankan
        # endpoint sinkron di thread pool.
        self._kunci = threading.Lock()

    def analisis(self, gambar: np.ndarray) -> list[WajahTerdeteksi]:
        with self._kunci:
            h, w = gambar.shape[:2]
            self._detektor.setInputSize((w, h))
            _, wajah = self._detektor.detect(gambar)
            if wajah is None:
                return []
            hasil = []
            for f in wajah:
                rapi = self._pengenal.alignCrop(gambar, f)
                emb = self._pengenal.feature(rapi).flatten().astype(np.float32)
                emb /= np.linalg.norm(emb) + 1e-12
                kotak = tuple(int(v) for v in f[:4])
                hidup = self._hidup.skor(gambar, kotak) if self._hidup else None
                titik = np.asarray(f[4:14], np.float32).reshape(5, 2)
                hasil.append(WajahTerdeteksi(kotak, float(f[-1]), emb, hidup, titik))
            return hasil


def cocokkan(
    embedding: np.ndarray,
    galeri: np.ndarray,
    label: list[str],
    ambang: float,
) -> tuple[str | None, float]:
    """Cari label dengan kemiripan kosinus tertinggi di galeri.

    `galeri` berbentuk (N, D) dan ternormalisasi; `label[i]` adalah NIM baris ke-i.
    Mengembalikan (nim, skor) atau (None, skor_terbaik) bila di bawah ambang.
    """
    if len(label) == 0:
        return None, 0.0
    skor = galeri @ embedding
    i = int(np.argmax(skor))
    terbaik = float(skor[i])
    return (label[i], terbaik) if terbaik >= ambang else (None, terbaik)
