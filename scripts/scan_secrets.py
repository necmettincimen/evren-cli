"""Tüm git geçmişini tarayıp evren_llm_ anahtarlarını ve şüpheli secret'ları raporlar."""
import re
import subprocess
import sys

PATTERNS = {
    "evren_api_key": re.compile(r"evren_llm_[A-Za-z0-9_\-]{10,}"),
    "generic_key": re.compile(r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{16,})"),
}


def run(args):
    return subprocess.run(args, capture_output=True, text=True, errors="replace")


def main():
    revs = run(["git", "rev-list", "--all"]).stdout.split()
    print(f"Taranan commit sayısı: {len(revs)}")
    found = {}
    for rev in revs:
        # her commit'teki tüm blob'ları listele
        tree = run(["git", "ls-tree", "-r", "-z", rev]).stdout
        for entry in tree.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) < 3:
                continue
            blob = parts[2]
            content = run(["git", "cat-file", "-p", blob]).stdout
            for name, pat in PATTERNS.items():
                for m in pat.finditer(content):
                    val = m.group(0) if name == "evren_api_key" else m.group(2)
                    if "your_api_key_here" in val or val in ("key1", "key2", "key3"):
                        continue
                    key = (name, val)
                    found.setdefault(key, set()).add((rev[:8], path))
    if not found:
        print("Hiçbir secret bulunamadı.")
        return 0
    print("\n=== BULUNAN SECRET'LAR ===")
    for (name, val), locs in sorted(found.items()):
        print(f"\n[{name}] {val}")
        for rev, path in sorted(locs):
            print(f"    {rev}  {path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
