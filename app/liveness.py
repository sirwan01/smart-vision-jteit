"""Deteksi wajah hidup (anti-spoofing pasif) dengan MiniFASNet.

Menggabungkan MiniFASNetV2 (skala potong 2,7) dan MiniFASNetV1SE (skala 4,0)
seperti metode Silent-Face-Anti-Spoofing (Minivision, Apache-2.0); bobot ONNX
dari yakhyo/face-anti-spoofing (Apache-2.0). Keluaran model: 3 kelas, indeks 1 = asli.

Model ini mengenali wajah yang difoto ulang dari cetakan atau layar di depan
kamera. Ia TIDAK dapat menolak berkas foto digital yang diunggah langsung ke API;
itu harus dicegah dengan API key dan membatasi pengirim hanya kamera kelas.
"""

from pathlib import Path

import cv2
import numpy as np

MODEL_HIDUP = {
    "minifasnet_v2.onnx": 2.7,
    "minifasnet_v1se.onnx": 4.0,
}
UKURAN_MASUKAN = 80


def potong_wajah(gambar: np.ndarray, kotak: tuple[int, int, int, int], skala: float) -> np.ndarray:
    """Potong area wajah yang diperluas `skala` kali dari pusatnya, lalu ubah ke 80x80."""
    tinggi, lebar = gambar.shape[:2]
    x, y, w, h = kotak
    w, h = max(w, 1), max(h, 1)
    skala = min((tinggi - 1) / h, (lebar - 1) / w, skala)
    lw, lh = w * skala, h * skala
    cx, cy = x + w / 2, y + h / 2
    x1, y1 = max(0, int(cx - lw / 2)), max(0, int(cy - lh / 2))
    x2, y2 = min(lebar - 1, int(cx + lw / 2)), min(tinggi - 1, int(cy + lh / 2))
    return cv2.resize(gambar[y1 : y2 + 1, x1 : x2 + 1], (UKURAN_MASUKAN, UKURAN_MASUKAN))


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


class DeteksiHidup:
    def __init__(self, model_dir: Path):
        self._model = []
        for nama, skala in MODEL_HIDUP.items():
            p = Path(model_dir) / nama
            if not p.exists():
                raise FileNotFoundError(
                    f"Model {p} belum ada. Jalankan: python scripts/unduh_model.py"
                )
            self._model.append((cv2.dnn.readNetFromONNX(str(p)), skala))

    def skor(self, gambar: np.ndarray, kotak: tuple[int, int, int, int]) -> float:
        """Probabilitas wajah asli (0–1), rata-rata kedua model."""
        total = 0.0
        for net, skala in self._model:
            masukan = potong_wajah(gambar, kotak, skala).astype(np.float32)
            net.setInput(masukan.transpose(2, 0, 1)[None])  # BGR, 0–255, NCHW
            total += float(_softmax(net.forward()[0])[1])
        return total / len(self._model)
