"""
Skills catalog for EVREN CLI.

Ports the Foreman "skills" idea to a coding assistant: a curated catalog of
reusable engineering frameworks/recipes. Each skill carries a short structured
prompt that is injected into the agent turn when applied, so the model follows
a proven procedure instead of improvising.

Skills are pure data (no I/O), so they are cheap to list and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    name: str
    title: str
    category: str
    description: str
    when_to_use: str
    steps: tuple[str, ...] = field(default_factory=tuple)

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


def list_skills(category: str | None = None) -> list[Skill]:
    """Returns skills, optionally filtered by category (case-insensitive)."""
    items = list(SKILLS.values())
    if category:
        cat = category.strip().lower()
        items = [s for s in items if s.category.lower() == cat]
    return sorted(items, key=lambda s: (s.category, s.name))


def get_skill(name: str) -> Skill | None:
    """Resolves a skill by exact name, then by unique normalized match."""
    if not name:
        return None
    key = name.strip().lower()
    if key in SKILLS:
        return SKILLS[key]
    # Normalized fallback: match on hyphen/space-insensitive name or title.
    norm = key.replace(" ", "-").replace("_", "-")
    matches = [
        s for s in SKILLS.values()
        if s.name == norm or s.title.lower() == key
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def categories() -> list[str]:
    """Returns the sorted list of distinct skill categories."""
    return sorted({s.category for s in SKILLS.values()})
