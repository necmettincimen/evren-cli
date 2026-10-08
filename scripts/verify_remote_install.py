"""Uzak kurulumun yapılandırma yüklemesini doğrular (salt-okuma).

Sunucuda `python3 scripts/verify_remote_install.py` ile çalıştırılır.
Yerel .env + ~/.evren-cli/config.json içeriğinin doğru okunduğunu ve
API'ye ulaşılabildiğini kontrol eder.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    ok = True

    # 1) Legacy config.json okunabiliyor mu (BOM'suz UTF-8 olmalı)
    cfg_path = Path.home() / ".evren-cli" / "config.json"
    if cfg_path.exists():
        raw = cfg_path.read_bytes()
        if raw[:3] == b"\xef\xbb\xbf":
            print("[WARN] config.json hala BOM iceriyor -> json.loads patlar")
            ok = False
        else:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            print(f"[OK] config.json -> anahtarlar: {sorted(data.keys())}")
            print(f"     SshHosts={len(data.get('SshHosts', {}))} "
                  f"PgConnections={len(data.get('PgConnections', {}))} "
                  f"Model={data.get('Model')}")
    else:
        print("[WARN] config.json bulunamadi")
        ok = False

    # 2) .env + config modulu
    from src import config

    print(f"[OK] cwd={Path.cwd()}")
    print(f"     api_key_yapilandirilmis={config.check_api_key_configured()}")
    print(f"     base_url={config.EVREN_BASE_URL}")
    print(f"     varsayilan_model={config.EVREN_DEFAULT_MODEL}")
    print(f"     ssl_verify={config.get_ssl_verify()}")
    if not config.check_api_key_configured():
        ok = False

    # 3) API erisim testi
    try:
        import httpx

        r = httpx.get(f"{config.EVREN_BASE_URL}/models",
                      headers={"Authorization": f"Bearer {config.EVREN_API_KEY}"},
                      verify=config.get_ssl_verify(),
                      timeout=25)
        print(f"[{'OK' if r.status_code == 200 else 'WARN'}] GET /models -> HTTP {r.status_code}")
        if r.status_code != 200:
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] API erisilemedi: {type(e).__name__}: {e}")
        ok = False

    print("SONUC:", "BASARILI" if ok else "EKSIK VAR")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
