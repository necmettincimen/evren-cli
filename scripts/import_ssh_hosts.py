"""~/.evren-cli/config.json içindeki SshHosts kaydını CLI formatına çevirir.

CLI, SSH hedeflerini `~/.evren/ssh_hosts.json` dosyasından okur (bkz.
src/ssh_hosts.py). Windows tarafındaki config.json ise host'ları
"SshHosts": { "alias": "user@host" } biçiminde tutar. Bu betik o kaydı
olduğu gibi taşır; yazma yetkisi güvenlik gereği kapalı bırakılır.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    src = Path.home() / ".evren-cli" / "config.json"
    if not src.exists():
        print(f"HATA: {src} bulunamadi")
        return 1

    cfg = json.loads(src.read_text(encoding="utf-8"))
    hosts: dict[str, dict] = {}

    for alias, target in (cfg.get("SshHosts") or {}).items():
        target = str(target).strip()
        if not target:
            continue
        user, _, host = target.rpartition("@")
        hosts[alias] = {
            "host": host or target,
            "user": user,
            "port": 22,
            "description": "Windows ~/.evren-cli/config.json'dan kopyalandi",
            "allow_write": False,
        }

    dst = Path.home() / ".evren" / "ssh_hosts.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(hosts, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"YAZILDI: {dst} ({len(hosts)} host)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
