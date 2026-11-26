"""Unduh model YuNet dan SFace (OpenCV Zoo) ke folder models/ dan periksa checksum."""

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import get_settings  # noqa: E402

BASIS = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
FAS = "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights"
MODEL = {
    "face_detection_yunet_2023mar.onnx": (
        f"{BASIS}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        f"{BASIS}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
    # Anti-spoofing MiniFASNet (Silent-Face, Apache-2.0)
    "minifasnet_v2.onnx": (
        f"{FAS}/MiniFASNetV2.onnx",
        "b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907",
    ),
    "minifasnet_v1se.onnx": (
        f"{FAS}/MiniFASNetV1SE.onnx",
        "ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676",
    ),
}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    folder = get_settings().model_dir
    folder.mkdir(parents=True, exist_ok=True)
    for nama, (url, hash_benar) in MODEL.items():
        tujuan = folder / nama
        if tujuan.exists() and sha256(tujuan) == hash_benar:
            print(f"[ada]  {nama}")
            continue
        print(f"[unduh] {nama}")
        sementara = tujuan.with_suffix(".part")
        urllib.request.urlretrieve(url, sementara)
        if sha256(sementara) != hash_benar:
            sementara.unlink()
            sys.exit(f"Checksum {nama} tidak cocok; unduhan dibatalkan")
        sementara.replace(tujuan)
    print("Model siap di", folder)


if __name__ == "__main__":
    main()
