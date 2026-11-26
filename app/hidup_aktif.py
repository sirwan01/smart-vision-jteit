"""Deteksi wajah hidup AKTIF: tantangan gerak kepala acak yang diverifikasi server.

Server membuat urutan acak (mis. tengah → kiri → tengah → kanan → tengah → kiri →
tengah). Klien mengirim bingkai webcam berkala; server menilai arah kepala tiap
bingkai dari 5 titik wajah YuNet dan hanya maju bila pose sesuai urutan. Foto diam,
layar, atau kamera virtual yang menayangkan gambar tidak dapat mengikuti urutan
acak ini. Selama tantangan, wajah harus tetap orang yang sama dan bingkai menghadap
depan juga dinilai anti-spoofing pasif (MiniFASNet).

"kiri"/"kanan" = sisi kiri/kanan MAHASISWA sendiri. Pada bingkai kamera mentah
(tidak dicerminkan) menoleh ke kiri menggeser hidung ke kanan gambar (yaw positif).
"""

from __future__ import annotations

import random
import secrets
import threading
import time
from dataclasses import dataclass, field

import numpy as np

AMBANG_TENGAH = 0.15   # |yaw| di bawah ini = menghadap depan
AMBANG_TOLEH = 0.35    # |yaw| di atas ini = menoleh
AMBANG_SAMA = 0.30     # kemiripan minimum dengan wajah acuan (orang yang sama)
JUMLAH_TOLEH = 3
BATAS_DETIK = 45
BATAS_BINGKAI = 150
BATAS_PALSU = 2        # bingkai depan yang dinilai palsu sebelum gagal

PETUNJUK = {
    "tengah": "Hadapkan wajah lurus ke kamera",
    "kiri": "Toleh ke KIRI Anda",
    "kanan": "Toleh ke KANAN Anda",
}


def arah_kepala(landmark: np.ndarray) -> float:
    """Rasio yaw dari 5 titik YuNet [mata, mata, hidung, mulut, mulut].

    ≈0 menghadap depan; positif = menoleh ke kiri orang tsb; negatif = ke kanan.
    """
    mata_a, mata_b, hidung = landmark[0], landmark[1], landmark[2]
    jarak = float(np.linalg.norm(mata_b - mata_a))
    if jarak < 1e-6:
        return 0.0
    return float(hidung[0] - (mata_a[0] + mata_b[0]) / 2) / jarak


def pose_dari_yaw(yaw: float) -> str | None:
    if abs(yaw) < AMBANG_TENGAH:
        return "tengah"
    if yaw > AMBANG_TOLEH:
        return "kiri"
    if yaw < -AMBANG_TOLEH:
        return "kanan"
    return None  # zona antara: belum jelas


def urutan_acak(rng: random.Random | None = None) -> list[str]:
    rng = rng or random.SystemRandom()
    langkah = ["tengah"]
    for _ in range(JUMLAH_TOLEH):
        langkah += [rng.choice(["kiri", "kanan"]), "tengah"]
    return langkah


@dataclass
class Tantangan:
    id: str
    tujuan: str                     # "presensi" | "pendaftaran"
    target: str                     # sesi_id atau nim
    pemilik: str                    # "pengguna:<id>" atau "perangkat"
    langkah: list[str]
    dibuat: float = field(default_factory=time.time)
    posisi: int = 0
    bingkai: int = 0
    palsu: int = 0
    status: str = "berjalan"        # berjalan | lulus | gagal
    alasan: str | None = None
    acuan: np.ndarray | None = None
    emb_depan: list = field(default_factory=list)
    hidup_depan: list = field(default_factory=list)
    dipakai: bool = False

    def publik(self) -> dict:
        selesai = self.status != "berjalan"
        sekarang = None if selesai else self.langkah[self.posisi]
        return {
            "id": self.id,
            "status": self.status,
            "alasan": self.alasan,
            "langkah": self.langkah,
            "posisi": self.posisi,
            "langkah_sekarang": sekarang,
            "petunjuk": PETUNJUK.get(sekarang) if sekarang else None,
            "sisa_detik": max(0, int(BATAS_DETIK - (time.time() - self.dibuat))),
        }

    def gagal(self, alasan: str) -> None:
        self.status, self.alasan = "gagal", alasan

    def embedding_akhir(self) -> np.ndarray:
        e = np.mean(self.emb_depan, axis=0).astype(np.float32)
        return e / (np.linalg.norm(e) + 1e-12)

    def skor_hidup_akhir(self) -> float | None:
        s = [x for x in self.hidup_depan if x is not None]
        return float(np.mean(s)) if s else None


def proses_bingkai(t: Tantangan, wajah: list, ambang_hidup: float) -> dict:
    """Perbarui status tantangan dengan satu bingkai. `wajah` = hasil FaceEngine.analisis."""
    if t.status != "berjalan":
        return {**t.publik(), "yaw": None, "pesan": None}
    t.bingkai += 1
    if time.time() - t.dibuat > BATAS_DETIK:
        t.gagal("Waktu habis")
    elif t.bingkai > BATAS_BINGKAI:
        t.gagal("Terlalu banyak bingkai")
    if t.status != "berjalan":
        return {**t.publik(), "yaw": None, "pesan": None}

    if len(wajah) != 1:
        pesan = "Wajah tidak terlihat" if not wajah else "Pastikan hanya satu wajah di depan kamera"
        return {**t.publik(), "yaw": None, "pesan": pesan}

    w = wajah[0]
    yaw = arah_kepala(w.landmark)
    pose = pose_dari_yaw(yaw)

    if t.acuan is not None and float(t.acuan @ w.embedding) < AMBANG_SAMA:
        t.gagal("Wajah berganti selama verifikasi")
        return {**t.publik(), "yaw": round(yaw, 3), "pesan": None}

    if pose == "tengah":
        palsu = w.skor_hidup is not None and w.skor_hidup < ambang_hidup
        if palsu:
            t.palsu += 1
            if t.palsu >= BATAS_PALSU:
                t.gagal("Terdeteksi foto/layar, bukan wajah langsung")
                return {**t.publik(), "yaw": round(yaw, 3), "pesan": None}
        else:
            if t.acuan is None:
                t.acuan = w.embedding
            t.emb_depan.append(w.embedding)
            t.hidup_depan.append(w.skor_hidup)

    harap = t.langkah[t.posisi]
    if pose == harap and (harap != "tengah" or t.acuan is not None):
        t.posisi += 1
        if t.posisi == len(t.langkah):
            t.status = "lulus"
    elif pose in ("kiri", "kanan") and harap in ("kiri", "kanan") and pose != harap:
        t.gagal("Arah toleh tidak sesuai petunjuk")

    return {**t.publik(), "yaw": round(yaw, 3), "pesan": None}


class PengelolaTantangan:
    """Penyimpanan tantangan di memori (jalankan server dengan satu proses worker)."""

    def __init__(self):
        self._data: dict[str, Tantangan] = {}
        self.kunci = threading.Lock()

    def buat(self, tujuan: str, target: str, pemilik: str) -> Tantangan:
        with self.kunci:
            kini = time.time()
            for k in [k for k, v in self._data.items() if kini - v.dibuat > BATAS_DETIK * 2]:
                del self._data[k]
            t = Tantangan(secrets.token_urlsafe(16), tujuan, target, pemilik, urutan_acak())
            self._data[t.id] = t
            return t

    def ambil(self, id_: str, pemilik: str) -> Tantangan | None:
        t = self._data.get(id_)
        return t if t is not None and secrets.compare_digest(t.pemilik, pemilik) else None

    def reset(self) -> None:
        with self.kunci:
            self._data.clear()


pengelola = PengelolaTantangan()
