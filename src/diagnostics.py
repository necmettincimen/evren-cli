"""
Diagnostics catalog for EVREN CLI.

Ports the Foreman "diagnostics" idea to a coding assistant: structured triage
systems for recurring engineering problems. Each diagnostic maps entry symptoms
to a set of triage questions, a diagnosis map, and routing to skills/playbooks.

Diagnostics are pure data (no I/O), so they are cheap to list and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Diagnostic:
    name: str
    title: str
    description: str
    entry_symptoms: tuple[str, ...] = field(default_factory=tuple)
    triage_questions: tuple[str, ...] = field(default_factory=tuple)
    possible_diagnoses: tuple[str, ...] = field(default_factory=tuple)
    routes_to_skills: tuple[str, ...] = field(default_factory=tuple)
    routes_to_playbooks: tuple[str, ...] = field(default_factory=tuple)

    def prompt_block(self, context: str = "") -> str:
        """Builds the triage instruction block injected into the agent turn."""
        lines = [
            f"TRIYAJ: {self.title} ({self.name})",
            f"Amaç: {self.description}",
            "Giriş belirtileri:",
        ]
        lines.extend(f"  - {s}" for s in self.entry_symptoms)
        lines.append("Triyaj soruları (sırayla sor, cevaplara göre daralt):")
        lines.extend(f"  {i}. {q}" for i, q in enumerate(self.triage_questions, 1))
        if self.possible_diagnoses:
            lines.append("Olası tanılar: " + ", ".join(self.possible_diagnoses))
        if self.routes_to_skills:
            lines.append("İlgili beceriler: " + ", ".join(self.routes_to_skills))
        if self.routes_to_playbooks:
            lines.append("İlgili playbook'lar: " + ", ".join(self.routes_to_playbooks))
        context = (context or "").strip()
        if context:
            lines.append(f"Bağlam: {context}")
        lines.append(
            "Önce kanıt topla (dosya oku, komut çalıştır), sonra en olası tanıyı "
            "seç ve ilgili beceri/playbook ile ilerle. Tahminle tanı koyma."
        )
        return "\n".join(lines)


DIAGNOSTICS: dict[str, Diagnostic] = {}


def _register(d: Diagnostic) -> None:
    DIAGNOSTICS[d.name] = d


_register(Diagnostic(
    name="build-failure",
    title="Derleme/Build Hatası Triyajı",
    description="Derleme veya paketleme neden başarısız oluyor sorusunu sistematik daraltır.",
    entry_symptoms=(
        "Build kırıldı",
        "Derleme hata veriyor",
        "CI kırmızı",
        "Bağımlılık çözülemiyor",
    ),
    triage_questions=(
        "Hata yerel mi yoksa yalnızca CI'da mı tekrarlıyor?",
        "Son çalışan sürümden bu yana hangi dosyalar/bağımlılıklar değişti?",
        "Hata mesajı sözdizimi, tip, bağlantı (link) yoksa bağımlılık kaynaklı mı?",
        "Temiz bir ortamda (cache temizlenerek) hata tekrarlıyor mu?",
    ),
    possible_diagnoses=(
        "syntax-error",
        "type-mismatch",
        "dependency-conflict",
        "stale-cache",
        "environment-drift",
    ),
    routes_to_skills=("root-cause-debug", "dependency-upgrade"),
    routes_to_playbooks=("hotfix",),
))

_register(Diagnostic(
    name="flaky-tests",
    title="Kararsız (Flaky) Test Triyajı",
    description="Bazen geçen bazen kalan testlerin kök nedenini bulur.",
    entry_symptoms=(
        "Test bazen geçiyor bazen kalıyor",
        "CI'da rastgele kırmızı",
        "Yerelde geçiyor CI'da kalmıyor",
    ),
    triage_questions=(
        "Test tek başına mı yoksa tüm paketle birlikte mi kararsız?",
        "Zamanlama, sıralama veya paylaşılan durum (global state) bağımlılığı var mı?",
        "Ağ, dosya sistemi veya saat gibi dış kaynaklara bağımlı mı?",
        "Paralel çalıştırmada mı ortaya çıkıyor?",
    ),
    possible_diagnoses=(
        "race-condition",
        "test-order-dependency",
        "shared-mutable-state",
        "external-resource-dependency",
        "timing-assumption",
    ),
    routes_to_skills=("root-cause-debug", "test-first"),
    routes_to_playbooks=(),
))

_register(Diagnostic(
    name="performance-regression",
    title="Performans Gerilemesi Triyajı",
    description="Yavaşlama veya kaynak artışının kaynağını ölçüme dayalı bulur.",
    entry_symptoms=(
        "Uygulama yavaşladı",
        "Yanıt süresi arttı",
        "Bellek/CPU kullanımı yükseldi",
    ),
    triage_questions=(
        "Gerileme ne zaman başladı ve o tarihten sonra ne değişti?",
        "Darboğaz CPU, bellek, I/O yoksa ağ mı? (profil/ölçüm ile)",
        "Yük altında mı yoksa her koşulda mı yavaş?",
        "Değişiklik geri alındığında performans düzeliyor mu?",
    ),
    possible_diagnoses=(
        "algorithmic-complexity",
        "n-plus-one-query",
        "memory-leak",
        "blocking-io",
        "cache-miss",
    ),
    routes_to_skills=("performance-tuning", "root-cause-debug"),
    routes_to_playbooks=(),
))

_register(Diagnostic(
    name="dependency-hell",
    title="Bağımlılık Çıkmazı Triyajı",
    description="Sürüm çakışmaları ve kırıcı yükseltmeleri sistematik çözer.",
    entry_symptoms=(
        "Sürüm çakışması",
        "Paket kurulamıyor",
        "Kırıcı değişiklik",
        "Peer dependency hatası",
    ),
    triage_questions=(
        "Çakışan paketler ve gerektirdikleri sürüm aralıkları neler?",
        "Çakışma doğrudan mı yoksa geçişli (transitive) bir bağımlılıktan mı?",
        "Yükseltme kırıcı (breaking) değişiklik içeriyor mu?",
        "Kilit dosyası (lockfile) tutarlı mı?",
    ),
    possible_diagnoses=(
        "version-conflict",
        "transitive-conflict",
        "breaking-change",
        "lockfile-drift",
    ),
    routes_to_skills=("dependency-upgrade", "root-cause-debug"),
    routes_to_playbooks=("migration",),
))

_register(Diagnostic(
    name="security-exposure",
    title="Güvenlik Açığı Triyajı",
    description="Olası bir güvenlik açığını önem ve etkiye göre daraltır.",
    entry_symptoms=(
        "Şüpheli girdi işleme",
        "Sır sızıntısı şüphesi",
        "Yetkisiz erişim riski",
        "Bağımlılık güvenlik uyarısı",
    ),
    triage_questions=(
        "Girdi nereden geliyor ve doğrulanıyor mu?",
        "Sırlar (anahtar/parola) kodda, logda veya repoda görünüyor mu?",
        "Yetkilendirme ve sınır denetimleri eksiksiz mi?",
        "Etkilenen bağımlılık sürümü bilinen bir CVE içeriyor mu?",
    ),
    possible_diagnoses=(
        "injection",
        "path-traversal",
        "secret-leak",
        "missing-authorization",
        "vulnerable-dependency",
    ),
    routes_to_skills=("security-review", "dependency-upgrade"),
    routes_to_playbooks=("hotfix",),
))

_register(Diagnostic(
    name="deploy-failure",
    title="Dağıtım (Deploy) Hatası Triyajı",
    description="Dağıtımın neden başarısız olduğunu veya geri alındığını daraltır.",
    entry_symptoms=(
        "Deploy başarısız",
        "Servis ayağa kalkmıyor",
        "Sağlık kontrolü (health check) geçmiyor",
        "Rollback gerekti",
    ),
    triage_questions=(
        "Hata yapılandırma, bağımlılık yoksa ortam farkından mı kaynaklanıyor?",
        "Yerel/staging ortamında aynı sürüm çalışıyor mu?",
        "Ortam değişkenleri ve sırlar doğru yüklendi mi?",
        "Sağlık kontrolü ve port/bağlantı ayarları doğru mu?",
    ),
    possible_diagnoses=(
        "config-mismatch",
        "missing-env-var",
        "port-binding",
        "migration-not-applied",
        "resource-limit",
    ),
    routes_to_skills=("root-cause-debug", "api-integration"),
    routes_to_playbooks=("hotfix", "release"),
))


def list_diagnostics() -> list[Diagnostic]:
    """Returns all diagnostics sorted by name."""
    return sorted(DIAGNOSTICS.values(), key=lambda d: d.name)


def get_diagnostic(name: str) -> Diagnostic | None:
    """Resolves a diagnostic by exact name, then by title (case-insensitive)."""
    if not name:
        return None
    key = name.strip().lower()
    if key in DIAGNOSTICS:
        return DIAGNOSTICS[key]
    matches = [d for d in DIAGNOSTICS.values() if d.title.lower() == key]
    if len(matches) == 1:
        return matches[0]
    return None


def match_by_symptom(text: str) -> list[Diagnostic]:
    """Returns diagnostics whose entry symptoms overlap the given text."""
    text = (text or "").strip().lower()
    if not text:
        return []
    hits: list[Diagnostic] = []
    for d in DIAGNOSTICS.values():
        for symptom in d.entry_symptoms:
            if symptom.lower() in text:
                hits.append(d)
                break
    return hits
