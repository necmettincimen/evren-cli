#!/usr/bin/env bash
# EVREN CLI (Antigravity-Style Assistant) — Linux/AlmaLinux Kurulum Betiği
# Kullanim: ./scripts/install.sh [--global]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${ROOT}/.venv"
GLOBAL=0
[[ "${1:-}" == "--global" ]] && GLOBAL=1

echo "=========================================================="
echo "  EVREN CLI Kurulumu (Linux)"
echo "=========================================================="

# 1. Python kontrolu
if ! command -v python3 >/dev/null 2>&1; then
  echo "HATA: python3 bulunamadi. 'sudo dnf install -y python3' ile kurun." >&2
  exit 1
fi
echo "[1/5] Python: $(python3 --version)"

# 2. Sanal ortam
echo "[2/5] Sanal ortam (.venv) hazirlaniyor..."
if [[ ! -d "${VENV}" ]]; then
  python3 -m venv "${VENV}"
fi

# 3. Bagimliliklar
echo "[3/5] Paketler yukleniyor..."
"${VENV}/bin/pip" install --upgrade pip --quiet
"${VENV}/bin/pip" install -r "${ROOT}/requirements.txt" --quiet
"${VENV}/bin/pip" install -e "${ROOT}" --quiet

# 4. .env
echo "[4/5] .env denetleniyor..."
if [[ ! -f "${ROOT}/.env" ]]; then
  cp "${ROOT}/.env.example" "${ROOT}/.env"
  echo "  .env olusturuldu. EVREN_API_KEY anahtarini girin!"
fi

# 5. Komut baglama
echo "[5/5] 'evren' komutu baglaniyor..."
if [[ "${GLOBAL}" -eq 1 ]]; then
  if [[ -w /usr/local/bin ]]; then
    ln -sf "${VENV}/bin/evren" /usr/local/bin/evren
    echo "  Global: /usr/local/bin/evren"
  else
    echo "  Global symlink icin sudo gerekli:"
    echo "    sudo ln -sf ${VENV}/bin/evren /usr/local/bin/evren"
  fi
else
  echo "  Kullanim: source ${VENV}/bin/activate && evren"
fi

echo "=========================================================="
echo "  Kurulum tamamlandi!"
echo "=========================================================="
