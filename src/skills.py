"""
Skills catalog for EVREN CLI.

Ports the Foreman "skills" idea to a coding assistant: a curated catalog of
reusable engineering frameworks/recipes. Each skill carries a short structured
prompt that is injected into the agent turn when applied, so the model follows
a proven procedure instead of improvising.

Built-in skills are pure data (no I/O). In addition, users can define their own
skills as markdown files with YAML-style front-matter under `<cli_home>/skills/`
(see `load_user_skills`); a user skill shadows a built-in with the same name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    category: str
    description: str
    when_to_use: str
    steps: tuple[str, ...] = field(default_factory=tuple)
    source: str = "builtin"  # "builtin" | "user"

    def prompt_block(self, context: str = "") -> str:
        """Builds the instruction block injected into the agent turn."""
        lines = [
            f"UYGULANAN BECERİ: {self.title} ({self.name})",
            f"Amaç: {self.description}",
            f"Ne zaman: {self.when_to_use}",
            "Adımlar:",
        ]
        lines.extend(f"  {i}. {step}" for i, step in enumerate(self.steps, 1))
        context = (context or "").strip()
        if context:
            lines.append(f"Bağlam: {context}")
        return "\n".join(lines)


# Catalog: name -> Skill. Names are lowercase, hyphenated, unique.
SKILLS: dict[str, Skill] = {}


def _register(skill: Skill) -> None:
    SKILLS[skill.name] = skill


_register(Skill(
    name="root-cause-debug",
    title="Kök Neden Hata Ayıklama",
    category="debugging",
    description="Belirtiyi değil, hatanın kök nedenini bulup düzeltir.",
    when_to_use="Bir hata/çökme/yanlış davranış bildirildiğinde.",
    steps=(
        "Belirtiyi tek cümleyle yeniden üret ve beklenen davranışı yaz.",
        "Hatayı en küçük yeniden üretim adımına indir (minimal repro).",
        "İlgili kod yolunu view_file/find_files ile oku; varsayım yapma.",
        "En olası 2-3 kök nedeni sırala ve her biri için kanıt topla.",
        "En olası nedeni cerrahi bir edit_file ile düzelt.",
        "Düzeltmeyi doğrulayan bir test/komut çalıştır.",
    ),
))

_register(Skill(
    name="surgical-refactor",
    title="Cerrahi Refactor",
    category="refactoring",
    description="Davranışı değiştirmeden kodu küçük, güvenli adımlarla iyileştirir.",
    when_to_use="Kod kokusu, tekrar veya okunabilirlik sorunu olduğunda.",
    steps=(
        "Mevcut davranışı koruyan testlerin varlığını doğrula; yoksa önce ekle.",
        "Değişikliği tek bir sorumluluğa indir (tek amaçlı refactor).",
        "edit_file ile yalnızca hedef bloğu değiştir; stil ve girintiyi koru.",
        "Her adımdan sonra testleri çalıştır; kırmızıysa geri al.",
        "Değişikliği kısa bir özetle ve diff ile raporla.",
    ),
))

_register(Skill(
    name="test-first",
    title="Önce Test (TDD)",
    category="testing",
    description="Önce başarısız testi yazar, sonra kodu testi geçecek şekilde ekler.",
    when_to_use="Yeni özellik veya hata düzeltmesi eklerken.",
    steps=(
        "Beklenen davranışı tanımlayan en küçük testi yaz.",
        "Testi çalıştır ve başarısız olduğunu doğrula (kırmızı).",
        "Testi geçecek en basit kodu yaz (yeşil).",
        "Kodu tekrar eden testlerle sağlamlaştır.",
        "Tüm test paketini çalıştır ve regresyon olmadığını doğrula.",
    ),
))

_register(Skill(
    name="api-integration",
    title="API Entegrasyonu",
    category="integration",
    description="Dış bir HTTP/LLM API'sini güvenli, dayanıklı biçimde entegre eder.",
    when_to_use="Yeni bir dış servis veya uç nokta bağlarken.",
    steps=(
        "Uç nokta sözleşmesini (istek/yanıt şeması) netleştir.",
        "Kimlik doğrulama ve hata durumlarını (4xx/5xx/timeout) ele al.",
        "Retry/backoff ve idempotency stratejisini belirle.",
        "İstemciyi küçük, test edilebilir bir modüle yaz.",
        "Ağ çağrısını mock'layan birim testleri ekle.",
    ),
))

_register(Skill(
    name="security-review",
    title="Güvenlik İncelemesi",
    category="security",
    description="Kodda yaygın güvenlik açıklarını sistematik olarak tarar.",
    when_to_use="Kullanıcı girdisi, dosya I/O veya kimlik doğrulama içeren kodda.",
    steps=(
        "Girdi noktalarını ve güven sınırlarını listele.",
        "Injection (komut/SQL/path traversal) risklerini denetle.",
        "Sırların (API anahtarı, parola) kodda/logda sızıp sızmadığını kontrol et.",
        "Yetkilendirme ve sınır denetimlerinin eksiksiz olduğunu doğrula.",
        "Bulguları önem sırasına göre ve düzeltme önerisiyle raporla.",
    ),
))

_register(Skill(
    name="performance-tuning",
    title="Performans İyileştirme",
    category="performance",
    description="Ölçüme dayalı, gereksiz optimizasyondan kaçınan performans çalışması.",
    when_to_use="Yavaşlık veya yüksek kaynak kullanımı bildirildiğinde.",
    steps=(
        "Önce ölç: darboğazı profil/benchmark ile tespit et.",
        "En pahalı sıcak yolu belirle; tahminle optimizasyon yapma.",
        "Tek bir iyileştirme uygula ve yeniden ölç.",
        "Kazanç yoksa değişikliği geri al.",
        "Sonucu önce/sonra metrikleriyle raporla.",
    ),
))

_register(Skill(
    name="code-review",
    title="Kod İncelemesi",
    category="quality",
    description="Bir değişikliği doğruluk, okunabilirlik ve risk açısından inceler.",
    when_to_use="Bir PR/diff gözden geçirilirken.",
    steps=(
        "Değişikliğin amacını ve kapsamını özetle.",
        "Doğruluk: kenar durumları ve hata yolları ele alınmış mı?",
        "Okunabilirlik: isimlendirme, karmaşıklık, tekrar.",
        "Testler: yeni davranış kapsanmış mı?",
        "Bulguları 'bloklayıcı / öneri / nit' olarak sınıflandır.",
    ),
))

_register(Skill(
    name="dependency-upgrade",
    title="Bağımlılık Güncelleme",
    category="maintenance",
    description="Bir bağımlılığı kırılma riskini yöneterek günceller.",
    when_to_use="Kütüphane sürümü yükseltilirken.",
    steps=(
        "Mevcut ve hedef sürüm arasındaki değişiklik notlarını oku.",
        "Kırıcı (breaking) değişiklikleri listele.",
        "Sürümü güncelle ve kodu uyumlu hale getir.",
        "Tüm testleri çalıştır; regresyonu doğrula.",
        "Kilit dosyasını (lockfile) tutarlı biçimde güncelle.",
    ),
))


# --------------------------------------------------------------------------- #
# User-defined skills (markdown files with front-matter under <cli_home>/skills)
# --------------------------------------------------------------------------- #

USER_SKILLS_DIRNAME = "skills"

# Cache of loaded user skills (lives in the session context; rebuilt by
# reload_user_skills()). USER_SKILLS aliases the default context's dict.
from src.session_context import default_context as _default_context

USER_SKILLS: dict[str, Skill] = _default_context().user_skills


def _user_skills() -> dict[str, Skill]:
    from src.session_context import current

    return current().user_skills

# Warnings collected during the last user-skill load (skipped/broken files).
USER_SKILL_WARNINGS: list[str] = []


def user_skills_dir(root: Path | None = None) -> Path:
    """Returns the directory holding user-defined skill files (never raises)."""
    if root is not None:
        return Path(root) / USER_SKILLS_DIRNAME
    try:
        from src.cli_home import cli_docs_dir

        base = cli_docs_dir()
    except Exception:  # pragma: no cover - cli_home must always import
        base = Path.home() / ".evren" / "cli_home"
    return base / USER_SKILLS_DIRNAME


def _normalize_skill_name(raw: str) -> str:
    """Lowercases and turns spaces/underscores into hyphens."""
    return " ".join(raw.strip().lower().replace("_", "-").replace("-", " ").split()).replace(" ", "-")


def _parse_skill_file(path: Path) -> tuple[Skill | None, str]:
    """Parses one skill markdown file.

    Format: optional `---` front-matter with `key: value` lines
    (name, title, category, description, when_to_use), then the body whose
    list lines (`- step` / `1. step`) become the skill steps.

    Returns (skill, warning); exactly one of them is set. A file without any
    usable content is skipped with a warning.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        return None, f"{path.name}: okunamadı ({e})"

    lines = text.replace("\r\n", "\n").split("\n")
    meta: dict[str, str] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                for raw in lines[1:i]:
                    raw = raw.strip()
                    if not raw or ":" not in raw or raw.startswith("#"):
                        continue
                    key, _, value = raw.partition(":")
                    meta[key.strip().lower()] = value.strip().strip("\"'")
                body_start = i + 1
                break

    steps: list[str] = []
    for raw in lines[body_start:]:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("- ", "* ")):
            steps.append(stripped[2:].strip())
        elif len(stripped) > 2 and stripped[0].isdigit():
            dot = stripped.find(".")
            paren = stripped.find(")")
            cut = min(x for x in (dot, paren) if x != -1)
            step = stripped[cut + 1:].strip()
            if step:
                steps.append(step)
        else:
            steps.append(stripped)

    name = _normalize_skill_name(meta.get("name", "")) or _normalize_skill_name(path.stem)
    if not name:
        return None, f"{path.name}: beceri adı türetilemedi, atlandı"
    if not steps and not meta.get("description"):
        return None, f"{path.name}: adım veya açıklama yok, atlandı"
    title = meta.get("title") or name
    return (
        Skill(
            name=name,
            title=title,
            category=(meta.get("category") or "user").strip() or "user",
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use", ""),
            steps=tuple(steps),
            source="user",
        ),
        "",
    )


def load_user_skills(directory: Path | None = None) -> tuple[list[Skill], list[str]]:
    """Loads all user skill files from `directory` (default: <cli_home>/skills).

    Broken or empty files are skipped with a warning; loading never raises.
    Returns (skills, warnings).
    """
    skills: list[Skill] = []
    warnings: list[str] = []
    directory = Path(directory) if directory is not None else user_skills_dir()
    try:
        files = sorted(directory.glob("*.md"))
    except OSError as e:  # pragma: no cover - unreadable directory
        return [], [f"{directory}: okunamadı ({e})"]
    for path in files:
        skill, warning = _parse_skill_file(path)
        if skill is None:
            warnings.append(warning)
        else:
            skills.append(skill)
    return skills, warnings


_USER_SKILLS_LOADED = False


def reload_user_skills(directory: Path | None = None) -> list[Skill]:
    """Rebuilds the module-level USER_SKILLS cache and returns its values."""
    global _USER_SKILLS_LOADED
    _USER_SKILLS_LOADED = True
    skills, warnings = load_user_skills(directory)
    cache = _user_skills()
    cache.clear()
    cache.update({s.name: s for s in skills})
    USER_SKILL_WARNINGS.clear()
    USER_SKILL_WARNINGS.extend(warnings)
    return list(cache.values())


def _merged_skills() -> dict[str, Skill]:
    """Built-ins merged with user skills (a user skill shadows the same name).

    User skills load lazily on first use, so `/skills` and `/skill` work no
    matter which part of the CLI touched the catalog first.
    """
    if not _USER_SKILLS_LOADED:
        try:
            reload_user_skills()
        except Exception:  # pragma: no cover - loading never raises by design
            pass
    merged = dict(SKILLS)
    merged.update(_user_skills())
    return merged


def list_skills(category: str | None = None) -> list[Skill]:
    """Returns built-in + user skills, optionally filtered by category."""
    items = list(_merged_skills().values())
    if category:
        cat = category.strip().lower()
        items = [s for s in items if s.category.lower() == cat]
    return sorted(items, key=lambda s: (s.category, s.name))


def get_skill(name: str) -> Skill | None:
    """Resolves a skill by exact name, then by unique normalized match."""
    if not name:
        return None
    merged = _merged_skills()
    key = name.strip().lower()
    if key in merged:
        return merged[key]
    # Normalized fallback: match on hyphen/space-insensitive name or title.
    norm = key.replace(" ", "-").replace("_", "-")
    matches = [
        s for s in merged.values()
        if s.name == norm or s.title.lower() == key
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def categories() -> list[str]:
    """Returns the sorted list of distinct skill categories."""
    return sorted({s.category for s in _merged_skills().values()})
