#!/usr/bin/env bash
# Dijalankan sekali saat Codespace dibuat: dependensi, model, dan akun admin demo.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python scripts/unduh_model.py

# Akun admin demo dengan kata sandi acak (tidak pernah ditulis ke repositori).
mkdir -p data
if ! .venv/bin/python scripts/kelola_pengguna.py daftar | grep -q '^admin '; then
  SANDI_BARU="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  export SANDI_BARU
  .venv/bin/python scripts/kelola_pengguna.py tambah admin --nama "Admin Demo" --peran admin
  umask 077
  printf 'Akun demo dasbor\nnama pengguna : admin\nkata sandi    : %s\n' "$SANDI_BARU" > data/akun-demo.txt
fi
echo "Penyiapan selesai. Akun demo: data/akun-demo.txt"
