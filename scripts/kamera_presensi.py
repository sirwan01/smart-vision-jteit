"""Klien kamera kelas: ambil bingkai webcam berkala dan kirim ke API presensi.

Contoh:
    python scripts/kamera_presensi.py --sesi 1 --api http://localhost:8000 --interval 3
"""

import argparse
import os
import time

import cv2
import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sesi", type=int, required=True, help="ID sesi yang sedang dibuka")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--kamera", default="0", help="Indeks webcam atau URL RTSP/HTTP")
    ap.add_argument("--interval", type=float, default=3.0, help="Detik antar-kiriman")
    args = ap.parse_args()

    sumber = int(args.kamera) if args.kamera.isdigit() else args.kamera
    kamera = cv2.VideoCapture(sumber)
    if not kamera.isOpened():
        raise SystemExit(f"Kamera {args.kamera} tidak dapat dibuka")

    header = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}
    url = f"{args.api}/sesi/{args.sesi}/presensi"
    print(f"Mengirim bingkai ke {url} setiap {args.interval} detik. Ctrl+C untuk berhenti.")
    with httpx.Client(timeout=30, headers=header) as klien:
        try:
            while True:
                ok, bingkai = kamera.read()
                if not ok:
                    print("Gagal membaca bingkai")
                    time.sleep(args.interval)
                    continue
                _, jpg = cv2.imencode(".jpg", bingkai, [cv2.IMWRITE_JPEG_QUALITY, 90])
                r = klien.post(url, files={"berkas": ("bingkai.jpg", jpg.tobytes(), "image/jpeg")})
                if r.status_code != 200:
                    print("Galat", r.status_code, r.text)
                    if r.status_code == 409:
                        break  # sesi ditutup
                else:
                    for d in r.json()["dikenali"]:
                        if d["baru_dicatat"]:
                            print(f"HADIR  {d['nim']}  (skor {d['skor']})")
                    for d in r.json().get("ditolak_palsu", []):
                        print(f"PALSU? {d['nim_dugaan'] or '-'}  (skor hidup {d['skor_hidup']})")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    kamera.release()


if __name__ == "__main__":
    main()
