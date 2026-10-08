"""
Output templates for EVREN CLI.

Ports the Foreman "output-templates" idea to a coding assistant: reusable
document skeletons for recurring engineering deliverables (postmortem, ADR,
PR description, release notes, runbook, incident report). Each template is a
Markdown skeleton the agent fills in, so output is consistent and complete.

Templates are pure data (no I/O), so they are cheap to list and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Template:
    name: str
    title: str
    audience: str
    description: str
    body: str = ""

    def prompt_block(self, context: str = "") -> str:
        """Builds the instruction block that asks the model to fill the template."""
        lines = [
            f"ÇIKTI ŞABLONU: {self.title} ({self.name})",
            f"Hedef kitle: {self.audience}",
            f"Amaç: {self.description}",
            "Aşağıdaki iskeleti eksiksiz doldur; boş alan bırakma, "
            "gereksiz bölüm ekleme. Kod/kanıt gerektiğinde gerçek dosya ve "
            "komut çıktılarına dayan.",
            "",
            "--- ŞABLON ---",
            self.body.strip(),
            "--- /ŞABLON ---",
        ]
        context = (context or "").strip()
        if context:
            lines.append(f"Bağlam: {context}")
        return "\n".join(lines)


TEMPLATES: dict[str, Template] = {}


def _register(t: Template) -> None:
    TEMPLATES[t.name] = t


_register(Template(
    name="incident-postmortem",
    title="Olay Sonrası Analiz (Postmortem)",
    audience="team",
    description="Bir olayın ne olduğunu, neden olduğunu ve tekrarını önleyecek düzeltmeleri belgeler.",
    body="""# Olay Sonrası Analiz: [Başlık]

**Önem:** [ ] SEV-1 [ ] SEV-2 [ ] SEV-3 | **Yazar:** [Ad] | **Tarih:** [Tarih]

## Özet
| Alan | Detay |
|------|-------|
| Ne oldu | [2-3 cümle] |
| Başlangıç / Tespit / Çözüm | [SS:DD] / [SS:DD] / [SS:DD] |
| Etki | [Etkilenen kullanıcı, gelir, SLA] |

## Zaman Çizelgesi
| Zaman | Olay | Aktör/Sistem | Kaynak |
|-------|------|--------------|--------|
| [SS:DD] | [Tetikleyici] | [Aktör] | [Log/uyarı] |

## Kök Neden (5 Neden)
| Seviye | Soru | Cevap |
|--------|------|-------|
| Neden 1 | [Olay] neden oldu? | [Çünkü...] |
| Neden 2 | [Cevap 1] neden oldu? | [Çünkü...] |
| Neden 3 | [Cevap 2] neden oldu? | [Çünkü...] |
| Neden 4 | [Cevap 3] neden oldu? | [Çünkü...] |
| Neden 5 | [Cevap 4] neden oldu? | [Çünkü...] |

**Kök neden:** [Tek cümle — sistemik neden, tetikleyici değil]

## Katkıda Bulunan Faktörler
- [ ] Süreç boşluğu: [...]
- [ ] İzleme boşluğu: [...]
- [ ] Bilgi boşluğu: [...]
- [ ] Test boşluğu: [...]

## Uygulanan Acil Düzeltmeler
| # | Düzeltme | Uygulayan | Kalıcı mı? |
|---|----------|-----------|------------|
| 1 | [...] | [Ad] | [ ] Evet [ ] Hayır |

## Gerekli Sistemik Düzeltmeler
| # | Düzeltme | Kök neden | Sahip | Termin | Durum |
|---|----------|-----------|-------|--------|-------|
| 1 | [...] | [...] | [Ad] | [Tarih] | [ ] Başlamadı |

## Çıkarılan Dersler
1. **[Başlık]:** [...]
""",
))

_register(Template(
    name="adr",
    title="Mimari Karar Kaydı (ADR)",
    audience="team",
    description="Önemli bir teknik kararı, gerekçesi ve sonuçlarıyla belgeler.",
    body="""# ADR-[NNN]: [Karar Başlığı]

**Durum:** [ ] Önerildi [ ] Kabul edildi [ ] Reddedildi [ ] Yerini aldı
**Tarih:** [Tarih] | **Karar verenler:** [İsimler]

## Bağlam
[Kararı gerektiren durum, kısıtlar ve problem.]

## Karar
[Alınan karar, net ve tek cümleyle.]

## Değerlendirilen Seçenekler
| Seçenek | Artılar | Eksiler |
|---------|---------|---------|
| [A] | [...] | [...] |
| [B] | [...] | [...] |

## Sonuçlar
- **Olumlu:** [...]
- **Olumsuz / ödünler:** [...]
- **Takip işleri:** [...]
""",
))

_register(Template(
    name="pr-description",
    title="Pull Request Açıklaması",
    audience="team",
    description="Bir değişikliği inceleyiciye net biçimde açıklar.",
    body="""# [Başlık]

## Ne değişti
[Değişikliğin özeti, 2-3 madde.]

## Neden
[Problemi ve motivasyonu açıkla. İlgili issue: #NNN]

## Nasıl test edildi
- [ ] Birim testleri: `[komut]`
- [ ] Manuel doğrulama: [adımlar]

## Kontrol Listesi
- [ ] Testler eklendi/güncellendi
- [ ] Dokümantasyon güncellendi
- [ ] Kırıcı değişiklik yok (varsa belirtildi)

## Ekran Görüntüsü / Çıktı
[Varsa]
""",
))

_register(Template(
    name="release-notes",
    title="Sürüm Notları",
    audience="user",
    description="Bir sürümdeki değişiklikleri kullanıcıya duyurur.",
    body="""# Sürüm [x.y.z] — [Tarih]

## Öne Çıkanlar
- [En önemli 1-3 değişiklik]

## Eklendi
- [...]

## Değişti
- [...]

## Düzeltildi
- [...]

## Kaldırıldı / Kırıcı Değişiklikler
- [...]

## Yükseltme Notları
[Gerekli adımlar, varsa]
""",
))

_register(Template(
    name="runbook",
    title="Operasyon Kılavuzu (Runbook)",
    audience="ops",
    description="Bir servisin çalıştırılması ve sorun giderilmesi için adım adım kılavuz.",
    body="""# Runbook: [Servis Adı]

## Genel Bakış
[Servisin amacı, bağımlılıkları, sahipleri.]

## Sağlık Kontrolü
- Uç nokta: `[URL]`
- Beklenen yanıt: [...]

## Yaygın Sorunlar ve Çözümleri
| Belirti | Olası neden | Çözüm |
|---------|-------------|-------|
| [...] | [...] | [...] |

## Dağıtım / Geri Alma
1. [Dağıtım adımı]
2. [Geri alma adımı]

## İletişim / Eskalasyon
[Kim, hangi kanal]
""",
))

_register(Template(
    name="bug-report",
    title="Hata Raporu",
    audience="team",
    description="Bir hatayı yeniden üretilebilir biçimde raporlar.",
    body="""# Hata: [Kısa başlık]

**Önem:** [ ] Kritik [ ] Yüksek [ ] Orta [ ] Düşük
**Ortam:** [OS, sürüm, tarayıcı/çalışma zamanı]

## Beklenen Davranış
[...]

## Gerçekleşen Davranış
[...]

## Yeniden Üretim Adımları
1. [...]
2. [...]

## Kanıt
[Log, hata mesajı, ekran görüntüsü]

## Olası Neden / Notlar
[...]
""",
))


def list_templates(audience: str | None = None) -> list[Template]:
    """Returns templates, optionally filtered by audience (case-insensitive)."""
    items = list(TEMPLATES.values())
    if audience:
        aud = audience.strip().lower()
        items = [t for t in items if t.audience.lower() == aud]
    return sorted(items, key=lambda t: (t.audience, t.name))


def get_template(name: str) -> Template | None:
    """Resolves a template by exact name, then by title (case-insensitive)."""
    if not name:
        return None
    key = name.strip().lower()
    if key in TEMPLATES:
        return TEMPLATES[key]
    matches = [t for t in TEMPLATES.values() if t.title.lower() == key]
    if len(matches) == 1:
        return matches[0]
    return None


def audiences() -> list[str]:
    """Returns the sorted list of distinct template audiences."""
    return sorted({t.audience for t in TEMPLATES.values()})
