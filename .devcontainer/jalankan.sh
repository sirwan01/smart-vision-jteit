#!/usr/bin/env bash
# Dijalankan setiap kali Codespace dibuka: menyalakan server (bila belum) dan
# menampilkan alamat HTTPS-nya.
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=8000
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  URL="https://${CODESPACE_NAME}-${PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/dasbor"
else
  URL="http://localhost:${PORT}/dasbor"
fi

cat <<INFO

  Dasbor presensi : ${URL}
  Akun demo       : lihat berkas data/akun-demo.txt
  Bagikan alamat  : tab PORTS -> port ${PORT} -> klik kanan -> Port Visibility
                    (Private = hanya akun GitHub Anda; Public = siapa pun yang tahu alamatnya)
  PERINGATAN      : khusus demo dengan data contoh/relawan, BUKAN data wajah seluruh mahasiswa.

INFO

if curl -fs "http://127.0.0.1:${PORT}/kesehatan" >/dev/null 2>&1; then
  echo "  Server sudah berjalan."
  exit 0
fi
echo "  Menyalakan server... (Ctrl+C untuk menghentikan)"
# --proxy-headers: Codespaces meneruskan HTTPS, sehingga cookie login bertanda Secure.
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers 1 \
  --proxy-headers --forwarded-allow-ips '*'
