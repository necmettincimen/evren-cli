"""
Playbooks for EVREN CLI.

Ports the Foreman "playbook" idea: multi-step recipes for recurring engineering
workflows (release, hotfix, onboarding, migration). A playbook has ordered
steps; the current checkpoint (playbook id + next step) is persisted so a
session can resume where it left off.

Playbook definitions are pure data. The checkpoint lives in `.evren/playbook.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_evren_dir

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Playbook:
    name: str
    title: str
    description: str
    steps: tuple[str, ...] = field(default_factory=tuple)

    def step_prompt(self, step_index: int, context: str = "") -> str:
        """Builds the instruction block for a single step (0-indexed)."""
        if step_index < 0 or step_index >= len(self.steps):
            raise IndexError(f"Adım aralık dışı: {step_index}")
        total = len(self.steps)
        lines = [
            f"PLAYBOOK: {self.title} ({self.name})",
            f"Amaç: {self.description}",
            f"Adım {step_index + 1}/{total}: {self.steps[step_index]}",
        ]
        context = (context or "").strip()
        if context:
            lines.append(f"Bağlam: {context}")
        return "\n".join(lines)


PLAYBOOKS: dict[str, Playbook] = {}


def _register(pb: Playbook) -> None:
    PLAYBOOKS[pb.name] = pb


_register(Playbook(
    name="release",
    title="Sürüm Çıkarma",
    description="Test edilmiş bir sürümü güvenli biçimde yayınlar.",
    steps=(
        "Tüm testleri çalıştır ve yeşil olduğunu doğrula.",
        "CHANGELOG.md'ye sürüm notlarını ekle.",
        "Sürüm numarasını pyproject.toml ve version.py'de artır.",
        "Değişiklikleri commit'le ve etiketle (tag).",
        "Uzak depoya push et ve sürümü doğrula.",
    ),
))

_register(Playbook(
    name="hotfix",
    title="Acil Hata Düzeltme",
    description="Üretimdeki kritik bir hatayı en kısa yoldan düzeltir.",
    steps=(
        "Hatayı yeniden üret ve etkisini sınırla.",
        "Kök nedeni tespit et (root-cause-debug becerisi).",
        "En küçük güvenli düzeltmeyi uygula.",
        "Düzeltmeyi doğrulayan bir regresyon testi ekle.",
        "Düzeltmeyi yayınla ve izlemeye al.",
    ),
))

_register(Playbook(
    name="onboarding",
    title="Projeye Katılım",
    description="Yeni bir kod tabanını hızlıca anlamak için sistematik keşif.",
    steps=(
        "Dizin yapısını ve giriş noktalarını tara (scan/list_directory).",
        "Bağımlılıkları ve çalıştırma komutlarını belirle.",
        "Ana veri akışını ve mimariyi özetle.",
        "Testleri çalıştır ve mevcut durumu doğrula.",
        "Bulguları proje hafızasına (memory) kaydet.",
    ),
))

_register(Playbook(
    name="migration",
    title="Kademeli Geçiş",
    description="Bir kütüphane/sürüm/API geçişini kırılmasız yürütür.",
    steps=(
        "Mevcut ve hedef arasındaki farkları ve kırıcı değişiklikleri listele.",
        "Geçişi küçük, geri alınabilir adımlara böl.",
        "Her adımda kodu uyarla ve testleri çalıştır.",
        "Eski yolu kaldır ve kalan referansları temizle.",
        "Tüm paketi doğrula ve geçişi belgele.",
    ),
))


def list_playbooks() -> list[Playbook]:
    """Returns all playbooks sorted by name."""
    return sorted(PLAYBOOKS.values(), key=lambda p: p.name)


def get_playbook(name: str) -> Playbook | None:
    """Resolves a playbook by exact name, then by title (case-insensitive)."""
    if not name:
        return None
    key = name.strip().lower()
    if key in PLAYBOOKS:
        return PLAYBOOKS[key]
    matches = [p for p in PLAYBOOKS.values() if p.title.lower() == key]
    if len(matches) == 1:
        return matches[0]
    return None


# --- Checkpoint persistence -------------------------------------------------

def _checkpoint_path(workspace_root: Path | None = None) -> Path:
    return get_evren_dir(workspace_root) / "playbook.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_checkpoint(workspace_root: Path | None = None) -> dict | None:
    """Loads the saved checkpoint, or None if there is none."""
    path = _checkpoint_path(workspace_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("playbook"):
        return None
    return data


def save_checkpoint(
    playbook_name: str,
    next_step: int,
    workspace_root: Path | None = None,
) -> dict:
    """Persists the checkpoint. Validates the playbook and step bounds."""
    pb = get_playbook(playbook_name)
    if pb is None:
        raise ValueError(f"Playbook bulunamadı: {playbook_name}")
    if next_step < 0 or next_step > len(pb.steps):
        raise ValueError(
            f"Adım aralık dışı: {next_step} (0..{len(pb.steps)})"
        )
    data = {
        "schema_version": SCHEMA_VERSION,
        "playbook": pb.name,
        "next_step": next_step,
        "total_steps": len(pb.steps),
        "updated_at": _now(),
    }
    path = _checkpoint_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return data


def clear_checkpoint(workspace_root: Path | None = None) -> bool:
    """Removes the checkpoint file. Returns True if one existed."""
    path = _checkpoint_path(workspace_root)
    if path.exists():
        path.unlink()
        return True
    return False
