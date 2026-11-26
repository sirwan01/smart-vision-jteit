"""Konfigurasi aplikasi, dibaca dari variabel lingkungan."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    db_path: Path
    model_dir: Path
    # Ambang kemiripan kosinus SFace (rekomendasi OpenCV: 0.363).
    ambang_kemiripan: float
    # Skor minimum deteksi wajah YuNet.
    ambang_deteksi: float
    # Bila diisi, setiap permintaan wajib membawa header X-API-Key.
    api_key: str | None
    # Deteksi wajah hidup (anti-spoofing) dan probabilitas "asli" minimum.
    anti_spoofing: bool
    ambang_hidup: float
    # Paksa atribut Secure pada cookie login (None = otomatis bila HTTPS).
    cookie_aman: bool | None


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.getenv("DB_PATH", ROOT / "data" / "presensi.db")),
        model_dir=Path(os.getenv("MODEL_DIR", ROOT / "models")),
        ambang_kemiripan=float(os.getenv("AMBANG_KEMIRIPAN", "0.363")),
        ambang_deteksi=float(os.getenv("AMBANG_DETEKSI", "0.8")),
        api_key=os.getenv("API_KEY") or None,
        anti_spoofing=os.getenv("ANTI_SPOOFING", "1").lower() not in ("0", "false", "tidak"),
        ambang_hidup=float(os.getenv("AMBANG_HIDUP", "0.5")),
        cookie_aman={"1": True, "0": False}.get(os.getenv("COOKIE_AMAN", "")),
    )
