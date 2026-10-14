# Plan: EVREN CLI'yi Akıllı Bağlamla Güçlendirme — agent.md / changelog.md / skills.md

Hedef: CLI her çalıştığında **çalıştığı proje dizininde** `agent.md` + `changelog.md` dosyalarını
eksikse oluşturur, varsa **yalnızca kendi yönettiği bölümleri** günceller; **CLI'nin kendi ana
dizininde** `skills.md` gibi destek dosyalarını barındırır; ve **prompt API'ye gitmeden önce** bu
dosyaları okuyup sistem bağlamına + prompt hazırlama (netleştirme/öneri) aşamasına enjekte eder.

Kapsam: yalnızca yerel Windows/cmd. SSH/uzak host yok. Yıkıcı silme yok. Mevcut içerik üzerine
körlemesine yazma yok.

---

## 0. Mevcut Yapı Özeti (salt-okuma keşifle doğrulandı)

| Katman | Dosya / Sembol | Bu plan için rolü |
|---|---|---|
| Çalışma dizini | `src/config.py::get_workspace_dir` (satır ~251) | `agent.md` / `changelog.md` hedef kökü = aktif proje dizini (`cwd` veya `-w`) |
| CLI ana dizini | `src/updater.py::get_project_root` → `Path(__file__).resolve().parents[1]` | `skills.md` vb. destek dosyalarının evi; **yazılabilir olmayabilir** → fallback şart |
| Evren dizini | `src/config.py::get_evren_dir`, `ensure_evren_dirs` | durum dosyası + yedekler burada; `.evren/.gitignore` hazır |
| Ortam bağlamı | `src/config.py::execution_context_block` | `agent.md` "Ortam" bölümünün tek doğruluk kaynağı (yeniden yazma, çağır) |
| Sistem prompt'u | `src/agent.py::AgentSession._compose_system` (~162) | Bloklar buraya ekleniyor: execution → decisions → memory → language. **Yeni bloklar aynı kalıba girer** |
| Bağlam tazeleme | `src/agent.py::refresh_context`, `apply_frame`, `clear_history`, `restore_checkpoint` | Dosya değişince bağlam yeniden kurulmalı; tüm bu yollar `_compose_system`'i çağırıyor (tek nokta) |
| Prompt hazırlama | `src/prompt_frame.py::request_frame_guidance` (~183) | Netleştirme + boş alan önerileri. `execution_context` argümanıyla zaten bağlam alıyor → **akıllı adım üretimi için docs özeti buraya da gider** |
| Kalıcı hafıza | `src/memory.py` (`.evren/memory.json`, `memory_context_block`) | Yeni docs blokları için **örnek mimari kalıp** (load → block → inject) |
| Beceriler | `src/skills.py` (saf veri, `SKILLS` dict) | Kod içi katalog; `skills.md` **kullanıcı/CLI-yeri katmanı** olarak eklenir, kataloğu ezmez |
| Diğer kataloglar | `src/templates.py`, `src/playbooks.py`, `src/diagnostics.py` | Aynı saf-veri kalıbı; docs blokları bunları tekrar etmez |
| Güvenli yazma | `src/file_ops.py::safe_write_file` (~293), `backup_file`, `is_safe_path`, `read_file_with_meta`, `PROTECTED_FILES` | Otomatik yedek + workspace sınırı + **CRLF/BOM koruması** — docs yazımı bunu kullanır |
| Sır maskeleme | `src/workflow_state.py::redact_text` | agent.md/changelog.md'ye sızmaya karşı **zorunlu ön filtre** |
| Proje haritası | `src/project_scanner.py::scan_workspace`, `generate_tree` | `agent.md` "Proje Haritası" bölümünün verisi (max_files/maks derinlik sınırlı) |
| REPL açılış | `src/repl.py::start_repl` (~534) | **Otomatik tetikleme noktası 1**: oturum açılırken |
| Görev bitişi | `src/repl.py::_run_agent` (~117) | **Otomatik tetikleme noktası 2**: görev sonrası changelog girişi |
| Tek seferlik ajan | `src/cli.py::cmd_agent` (~159) | **Otomatik tetikleme noktası 3**: `evren agent` akışı |
| Kip geçidi | `src/modes.py`, `src/tool_gateway.py::check_tool_allowed` | `plan`/`ask` modunda otomatik **yazma yapılmaz** (salt-okuma sözü korunur) |
| Araç şeması | `src/tools.py::TOOLS_SCHEMA` | İsteğe bağlı yeni araçlar: `update_agent_doc`, `log_change` |
| Slash komutlar | `src/repl.py` (`/skills`, `/memory`, `/checkpoint` kalıpları) | `/docs` komutu aynı kalıpla eklenir |
| Zaten var olanlar | repo kökünde `AGENTS.md`, `CHANGELOG.md` | **Çakışma riski:** `agent.md` ≠ `AGENTS.md` (Windows'ta case-insensitive FS!) — bkz. §7 R1 |

Mimari ilke: **docs = önbellek, gerçeklik = canlı ortam.** Her oturumda dosya okunur, canlı ortamla
karşılaştırılır, yalnızca fark varsa yazılır. Dosya asla tek doğruluk kaynağı sayılmaz; ajana
"önce salt-okuma ile doğrula" notu eşlik eder.

---

## 1. Hedef Dosyalar

### 1.1 Proje dizinine (çalışılan depo)
| Dosya | İçerik | Yazan | İzin durumu |
|---|---|---|---|
| `agent.md` | CLI'nin o projedeki kimliği/ortamı/haritası/kuralları; **yalnızca yönetilen bloklar** otomatik | `src/project_docs.py` | eksikse oluştur; varsa sadece `evren:managed` blokları |
| `changelog.md` | Oturum/görev bazlı, kronolojik, **append-only** değişiklik günlüğü | `src/project_docs.py` | eksikse başlık + yönetilen blok; girişler sona eklenir |

### 1.2 CLI ana dizinine (kurulu paket kökü)
| Dosya | İçerik |
|---|---|
| `skills.md` | Kod içi `SKILLS` kataloğunun markdown aynası + `## Özel Beceriler` (elle düzenlenebilir, CLI **ezmez**) |
| `lessons.md` | CLI'nin oturumlar arası edindiği kısa dersler (append-only, yönetilen blok) |
| `commands.md` | Slash komut + alt komut referansı (yönetilen blok, `repl` help'ten türetilir) |
| `context/README.md` | Bu klasörün ne olduğu + elle ne eklenebilir |

Yol çözümleme (`src/cli_home.py::cli_docs_dir`):
1. `get_project_root()` yazılabilir ve bir `pyproject.toml`/`.git` içeriyorsa → orası (geliştirme kurulumu).
2. Değilse → `Path.home() / ".evren" / "cli_home"` (pip/Program Files kurulumu; **Windows'ta site-packages çoğu zaman salt-okunur**).
3. Hiçbiri yazılabilir değilse → docs katmanı tamamen sessizce devre dışı (CLI asla çökmez).

### 1.3 Durum dosyası
`.evren/docs_state.json` — yönetilen blok hash'leri, son oturum, son changelog imzası. Şema:
```json
{
  "schema_version": 1,
  "updated_at": "2025-01-01T00:00:00+00:00",
  "sections": {"agent.runtime_env": "sha256:1a2b3c...", "agent.map": "sha256:..."},
  "last_changelog_entry": "sha256:...",
  "session": {"started_at": "...", "model": "qwen3.8-flash-next", "workspace": "D:\\..."}
}
```

---

## 2. Runtime Entegrasyon Noktaları (kesin satır hedefleri)

| # | Dosya | Fonksiyon | Değişiklik |
|---|---|---|---|
| E1 | `src/project_docs.py` (YENİ) | — | `ensure_project_docs()`, `agent_md_context_block()`, `changelog_context_block()`, `append_changelog_entry()`, `unmark_docs()` |
| E2 | `src/cli_home.py` (YENİ) | — | `cli_docs_dir()`, `ensure_cli_docs()`, `cli_docs_context_block()` |
| E3 | `src/docs_merge.py` (YENİ) | — | marker tabanlı bölüm oku/yaz/çıkar + hash; **saf, I/O'suz çekirdek** (test edilebilirlik) |
| E4 | `src/agent.py` | `_compose_system` (~162) | `docs` bloklarını `execution_context_block` sonrasına ekle (mevcut `try/except` kalıbıyla) |
| E5 | `src/agent.py` | `run_step` (~421) | girişte `ensure_project_docs(refresh=True)`; çıkışta `append_changelog_entry(...)` — **yalnızca `effective_mode == NORMAL`** |
| E6 | `src/prompt_frame.py` | `request_frame_guidance` (~183) | system satırına `docs_digest` (kısa, ~1200 kr) ekle → netleştirme/öneriler projeyi bilerek üretilir |
| E7 | `src/repl.py` | `start_repl` (~534) | `session` kurulduktan sonra `ensure_project_docs()` + `ensure_cli_docs()`; banner'a tek satır bilgi |
| E8 | `src/repl.py` | `_run_agent` (~117) | görev sonrası changelog flush (E5 ile çakışmayı önlemek için **tek sahip**: agent.py yazar, repl sadece bildirir) |
| E9 | `src/cli.py` | `cmd_agent` (~159) | `AgentSession` oluşturmadan önce `ensure_project_docs()` |
| E10 | `src/cli.py` | `main` (~326) | yeni `evren docs [status|refresh|unmark|path]` alt komutu |
| E11 | `src/repl.py` | slash dispatch (~758 civarı) | `/docs [status|refresh|unmark]` |
| E12 | `src/tools.py` + `src/tool_gateway.py` | `TOOLS_SCHEMA`, `check_tool_allowed` | **Faz 7 (opsiyonel):** `update_agent_doc`, `log_change` araçları; `plan` modunda yasak |
| E13 | `src/config.py` | `ensure_evren_dirs` (~287) | `.evren/.gitignore` içeriğine `docs_state.json` satırını **eksikse** ekle (mevcut satırları koru) |

Enjeksiyon sırası (system prompt): `base → execution → **agent.md** → **changelog (son N)** → **cli skills/lessons** → decisions → memory → language`.
Gerekçe: proje kimliği en yüksek öncelik; kişisel hafıza sonra gelir.

---

## 3. Prompt Öncesi Bağlam Yükleme Akışı

```
evren (REPL) / evren agent
  └─ start_repl | cmd_agent
       ├─ ensure_cli_docs()        # CLI ana dizini: skills.md, lessons.md, commands.md (eksikse oluştur)
       ├─ ensure_project_docs()    # proje: agent.md, changelog.md (eksikse oluştur; değiştiyse yönetilen blokları güncelle)
       │    ├─ scan_workspace()+generate_tree() → proje haritası verisi
       │    ├─ execution_context_block()        → ortam verisi
       │    └─ docs_merge: hash farklı mı?  hayır → HİÇ YAZMA (idempotent, gürültüsüz)
       └─ AgentSession.__init__ → _compose_system()
            └─ agent.md / changelog / cli docs blokları system prompt'ta (token bütçeli)

Kullanıcı Brief yazar
  └─ _collect_frame → request_frame_guidance(frame, clarify, execution_context + docs_digest)
       → netleştirme sorusu ve alan önerileri PROJENİN KURALLARINI/HARİTASINI BİLİR

Görev yürür (run_step)
  └─ bitişte: yürütülen araç çağrılarından (edit_file/write_file/run_command) dosya/komut özeti
       → append_changelog_entry(giriş)   # yalnızca normal modda, redact_text'ten geçmiş
```

Akıllı adım üretimi kuralları (prompt metnine eklenecek):
- `agent.md` kuralları ile sistem prompt'u çelişirse **sistem prompt'u kazanır**; çelişkiyi kullanıcıya bildir.
- `changelog.md` "son işler" girdileri **eski kabul edilebilir**; uygulamadan önce canlı doğrulama yap.
- `skills.md` özel becerileri, kod içi katalogla aynı addaysa kullanıcıki öncelikli ama adım sayısı 8'i geçemez.
- Docs'ta yazan dosya yolu artık yoksa "harita bayat" de ve yeniden tara.

Token bütçesi (`src/token_manager.py` ile tahmin): docs bloklarına toplamda **~3500 token** üst sınırı.
Aşım kırpma sırası: `changelog` (son 5→3→0) → `cli lessons` → `agent.md` harita bölümü → `agent.md` kalanı.
Kırpma gerçekleşirse bloğun sonuna `[…docs kırpıldı: N kr]` yazılır (model gizli kırpma olduğunu bilir).

---

## 4. Dosya Şemaları (şablonlar)

### 4.1 `agent.md` (proje dizini)
```markdown
# agent.md — <proje adı>

<!-- evren:managed:start section=identity -->
Bu dosya EVREN CLI tarafından otomatik yönetilir. Aşağıdaki **yönetilen** bloklar CLI her
çalıştığında güncellenir; bu blokların dışındaki metin korunur ve ajana bağlam olarak verilir.
<!-- evren:managed:end section=identity -->

## Amaç
<elle yazılır; CLI dokunmaz>

<!-- evren:managed:start section=runtime_env -->
- Çalışma alanı: D:\Github\<repo>
- İşletim sistemi / kabuk: Windows / cmd.exe (PowerShell için `powershell -NoProfile -Command`)
- Python: 3.x.x | CLI sürümü: x.y.z | Varsayılan model: qwen3.8-flash-next
- Git dalı: <branch> | son commit: <short> (<date>)   # git yoksa "git yok"
<!-- evren:managed:end section=runtime_env -->

<!-- evren:managed:start section=project_map -->
- Dil dağılımı: .py 34, .md 5, .toml 1
- Giriş noktası: src/cli.py (console script: evren) | src/__init__.py
- Modüller: src/ (32 dosya), tests/ (28 dosya), scripts/ (6 dosya)
- Komutlar: `python -m pytest -q` | `python -m compileall src`
- Ağaç (derinlik 2): <generate_tree çıktısı, en fazla 60 satır>
<!-- evren:managed:end section=project_map -->

<!-- evren:managed:start section=conventions -->
- Değiştirme öncesi view_file; hedefli edit_file; sonrası test.
- Yeni kod mevcut stille aynı: Türkçe docstring, `from __future__ import annotations`, tipli imza.
- Sır/anahtar dosyaları (`PROTECTED_FILES`) ve `.env` düzenlenmez.
- Windows: yol ayıracı `\`, CRLF; `safe_write_file` BOM/CRLF'yi korur.
<!-- evren:managed:end section=conventions -->

<!-- evren:managed:start section=current_focus -->
- Son oturum: <tarih> | son görev: <goal ilk 120 kr> | durum: <tamam/blokaj>
- Açık engeller: <tracking.blockers varsa>
<!-- evren:managed:end section=current_focus -->

## Notlar (elle)
<buraya yazılanlar korunur; CLI buraya asla yazmaz>
```

### 4.2 `changelog.md` (proje dizini)
```markdown
# Changelog — <proje adı>
Bu dosya EVREN CLI tarafından **append-only** güncellenir; eski girişler değiştirilmez/silinmez.

<!-- evren:managed:start section=recent -->
## Son Oturumlar (otomatik özet, CLI tarafından yeniden yazılır)
- 2025-01-01 14:03 | agent | görev: "..." | dosya: src/x.py (+12/-3) | test: pytest 218 passed
<!-- evren:managed:end section=recent -->

## 2025-01-01 14:03 UTC — EVREN CLI <sürüm>
- Görev: <brief ilk 160 kr>
- Değişen dosyalar: `src/project_docs.py` (yeni), `src/agent.py` (düzenlendi)
- Çalıştırılan doğrulama: `python -m pytest tests/test_project_docs.py -q` → 7 passed
- Not: <ajan son yanıtından tek satır; sır maskelenmiş>
- Geri alma: `evren rollback src/agent.py` | yedekler `.evren/backups/`
```

### 4.3 `skills.md` (CLI ana dizini)
```markdown
# skills.md — EVREN CLI Beceri Kataloğu

<!-- evren:managed:start section=catalog -->
Kod içi katalog: `src/skills.py` (8 beceri). Aşağıdaki tablo onun üretilmiş kopyasıdır.
| Beceri | Kategori | Ne zaman |
|---|---|---|
| root-cause-debug | debugging | hata/çökme bildirildiğinde |
| ... | | |
<!-- evren:managed:end section=catalog -->

## Özel Beceriler            <!-- CLI bu bölümü ASLA değiştirmez; okur ve bağlama katar -->
### my-deploy-check
Amaç: ... | Ne zaman: ... | Adımlar: 1) ... 2) ... 3) ...
```

### 4.4 `lessons.md` (CLI ana dizini)
```markdown
# lessons.md — CLI Oturum Dersleri
<!-- evren:managed:start section=lessons -->
- [2025-01-01] Windows cmd'de `&&` yerine ayrı komut; PowerShell gerekçesi açıkça çağrılır.
<!-- evren:managed:end section=lessons -->
```
Kural: en yeni 40 satır korunur, eskiler `.evren/logs/lessons_archive.md`'e **taşınır** (silinmez).

### 4.5 `commands.md` (CLI ana dizini)
Yönetilen blok: `repl` help + `cli.py` subparser'larından türetilen tek tablo. Elle bölüm: `## Takma Adlar`.

---

## 5. Otomatik Oluşturma / Güncelleme Mantığı

`ensure_project_docs(root, *, refresh=False) -> DocsResult`:
1. `enabled()` kontrolü: `EVREN_AUTO_DOCS` (varsayılan `true`), `EVREN_AGENT_DOC`, `EVREN_CHANGELOG`;
   ayrıca `.evren/docs.json` içinde `{"disabled": true}`. Kip `plan`/`ask` ise **yalnızca oku, yazma**.
2. `agent.md` yok → şablonu yaz (`safe_write_file`, `create_backup=False` çünkü dosya yeni).
   `AGENTS.md` var ama `agent.md` yoksa → şablonun "Amaç" bölümüne `AGENTS.md`'ye atıf satırı koy
   (**AGENTS.md'yi asla ezme/okuma-yazma konusu yapma** — bkz. R1).
3. Var olan dosyada `evren:managed` marker'larını ara:
   - marker yok ama dosya dolu → dosyayı **dokunulmaz** say, `.evren/logs/docs_warn.log`'a neden yaz,
     `/docs` çıktısında "yönetilen blok yok, eklemek için `/docs refresh --adopt`" öner (onaylı).
   - marker var → her bölüm için yeni içerik üret, `sha256` karşılaştır, **farklıysa** o bölümü değiştir.
4. Yazma öncesi `read_file_with_meta` → `preserve_meta` ile CRLF/BOM geri yazılır.
5. Yazma `safe_write_file(create_backup=True)` ile → `.evren/backups/agent.md.<ts>.bak`.
6. İçerik `redact_text()`'ten geçirilir (API anahtarı, `user:pass@host`, Bearer, private key).
7. `docs_state.json` güncellenir (hash'ler + oturum).
8. Herhangi bir OSError/PermissionError → **uyarı yaz, devam et**. Docs özelliği CLI'yi asla düşürmez.

`append_changelog_entry(root, entry)`:
- `run_step` boyunca toplanan araç izleri (`edit_file`/`write_file` hedefleri, `run_command` komut +
  çıkış kodu, test özeti) tek girdiye dönüştürülür.
- İmza = `sha256(goal + sorted(files) + exit_codes)`; `docs_state.last_changelog_entry` ile aynıysa
  **yazma** (aynı görevin tekrarında çift girdi olmaz).
- `## Son Oturumlar` yönetilen bloğu son 10 girdiyle yeniden yazılır; tam girdi dosyanın sonuna eklenir.
- Girdi boyut sınırı: 1200 karakter; aşarsa kes ve `…` ekle.

Zamanlama özeti:
| Olay | agent.md | changelog.md | CLI docs |
|---|---|---|---|
| REPL açılış / `evren agent` başlangıç | oluştur/güncelle | başlık yoksa oluştur | oluştur/güncelle |
| Her görev bitişi (normal mod) | `current_focus` bölümü | yeni girdi + özet blok | `lessons.md` (yalnızca başarısız/blokajlı görevde) |
| `/docs refresh` | zorla yeniden üret | — | zorla yeniden üret |
| `/docs unmark` (onaylı) | yönetilen blokları kaldır | yönetilen blokları kaldır | — |

---

## 6. Windows / cmd Notları

- **Case-insensitive FS:** `agent.md` ile `AGENTS.md` aynı dizinde **tek dosya sayılmaz** (farklı adlar)
  ama `Path("agent.md")` açılışı `AGENTS.md`'yi döndürebilir (NTFS son eşleşme). Bu yüzden hedef ad
  çözümlemesi `iterdir()` ile **birebir büyük/küçük harf duyarsız + tam ad** kontrolü yapar; `AGENTS.md`
  varsa `agent.md` yerine `agent.md` **oluşturulur ama AGENTS.md'ye yazılmaz**, ve ajana iki dosyanın
  da bağlamda gösterileceği bildirilir. Çakışma kesin çözülünceye kadar `agent.md`'ye yönetilen blok
  eklenirken AGENTS.md içeriği salt-okunur referans olarak korunur.
- Yollar: her zaman `Path` + `resolve()`; prompt'larda `\` kaçışını azaltmak için `as_posix()` kullan.
- Kabuk: `cmd.exe` varsayılan; plan/metinlerde PowerShell örnekleri `powershell -NoProfile -Command "..."`
  biçiminde, tırnaklar `\"` ile kaçırılmış olmalı.
- Kodlama: dosyalar **UTF-8**; okuma `utf-8-sig` (BOM toleransı), yazma `newline=""` (mevcut `safe_write_file`
  davranışı). Konsol `cp857/cp437` → `cli.py` zaten `reconfigure(encoding="utf-8")` yapıyor; docs çıktıları
  ASCII-fallback güvenli tutulur (emoji yok, `•` yerine `-` tercih).
- Uzun yol (>260 kr): yazma hatalarında `\\?\` prefix'i denenmez; bunun yerine hata net bildirilir ve
  docs devre dışı bırakılır.
- Antivirüs/OneDrive klasörlerinde `PermissionError` beklentisi: 1 kez retry, sonra sessiz geç.
- Program Files / `site-packages` kurulumunda CLI ana dizinine yazma reddi → `~/.evren/cli_home` fallback (şart).
- `.evren/.gitignore` zaten `backups/ logs/` içeriyor; `docs_state.json` eklenir. `agent.md` ve
  `changelog.md` **commit edilebilir** tasarım (takım için faydalı) — bu yüzden sır maskeleme zorunlu.

---

## 7. Riskler ve Geri Alma

| # | Risk | Etki | Önlem | Geri alma |
|---|---|---|---|---|
| R1 | `agent.md` / `AGENTS.md` NTFS büyük/küçük harf çakışması | mevcut kurallar dosyası kaybolur | `iterdir()` ile tam ad tespiti; `AGENTS.md` **asla yazma hedefi değil**; hedef `agent.md` | `.evren/backups/agent.md.*.bak`; `evren rollback agent.md` |
| R2 | Kullanıcının elle yazdığı içerik silinir | veri kaybı | yalnızca `evren:managed` blokları yazılır; blok dışı bytes kopyalanır; marker'sız dosyaya dokunulmaz | yedek + `/docs unmark` |
| R3 | Sır (API anahtarı, parola) docs'a sızar ve commit edilir | güvenlik ihlali | `redact_text()` zorunlu; yazma sonrası `scripts/scan_secrets.py` benzeri kontrol; `.env` hedeflenmez | `evren rollback`, sonra git revert |
| R4 | Her oturumda dosya değişimi → git diff gürültüsü | review yorgunluğu | hash karşılaştırması: fark yoksa yazma; `current_focus` yalnızca goal değişirse güncellenir | `EVREN_AUTO_DOCS=false` |
| R5 | Bağlam şişmesi → token maliyeti / kesilmeler | pahalı, kalitesiz yanıt | ~3500 token üst sınırı + tanımlı kırpma sırası + `/tokens` ile ölçüm | `EVREN_DOCS_TOKENS=0` (docs bağlamı kapalı, dosyalar yine yazılır) |
| R6 | Bayat `agent.md` ajana yanlış gerçeklik verir | yanlış karar | her bloğa "canlı ortam kazanır" notu; yol yoksa "harita bayat" kuralı | `/docs refresh` |
| R7 | Salt-okuma kipi ihlali (`plan`/`ask`) | sözleşme bozulur | kip kontrolü `ensure_project_docs` içinde; `tool_gateway` yeni araçları plan modunda reddeder | — |
| R8 | Yazılabilir olmayan CLI ana dizini | açılışta istisna | 3 kademeli fallback + tüm docs kodu `try/except OSError` | `EVREN_CLI_HOME=<yazılabilir dizin>` |
| R9 | Ajan kendi `agent.md`'sini düzenleyip kendini kısıtlar | döngüsel davranış | yönetilen bloklara `edit_file` ile yazma girişimi uyarı üretir; kullanıcı onayı ister | yedek |
| R10 | Paralel iki CLI oturumu aynı dosyaya yazar | bozuk blok | yazma öncesi `.evren/docs.lock` (best-effort, 5 sn TTL, stale temizliği); başarısızsa bu oturum atlar | lock silinir |

Genel geri alma: her yönetilen yazım `safe_write_file` yedeği üretir → `evren rollback agent.md` /
`evren rollback changelog.md`. Toplam kapatma: `.env` içine `EVREN_AUTO_DOCS=false`.
Özellik tamamen silinebilir: 3 yeni modül + 6 dosyadaki küçük ekleme; `git revert` tek commit ile temiz.

---

## 8. Adım Adım Uygulama Fazları (her biri bağımsız doğrulanabilir)

### Faz 0 — Kilit teyidi (salt-okuma, 1 tur)
- Hedef: §2 tablosundaki satır hedeflerinin hâlâ geçerli olduğunu `view_file` ile teyit et;
  `tests/helpers.py` ve `tests/test_memory.py` kalıplarını oku (test yazım stili için).
- Araç: `view_file`, `run_command` (pytest salt-okuma).
- Kontrol: `python -m pytest -q` mevcut durumda **yeşil** (temiz taban çizgisi kaydedilir).

### Faz 1 — `src/docs_merge.py` (saf çekirdek, I/O yok)
- Hedef: `render_sections(text, {name: body}) -> (new_text, changed_names)`, `extract_sections(text)`,
  `strip_managed(text)`, `section_hash(body)`; bozuk/iç içe marker'da **istisna yerine güvenli ret**.
- Kontrol: `tests/test_docs_merge.py` — blok dışı içerik byte-byte korunur, CRLF korunur,
  marker yoksa `changed_names == []`, aynı içerikte `changed_names == []` (idempotent).

### Faz 2 — `src/cli_home.py` + CLI destek dosyaları
- Hedef: `cli_docs_dir()` 3 kademeli çözüm; `ensure_cli_docs()` → `skills.md`, `lessons.md`,
  `commands.md`, `context/README.md` (eksikse oluştur, `## Özel Beceriler` bölümü korunur).
- `skills.md` kataloğu `src/skills.py::list_skills()`'ten üretilir (elle kopya yok).
- Kontrol: `tests/test_cli_home.py` — `EVREN_CLI_HOME` tmp_path'a yönlenir; mevcut `## Özel Beceriler`
  metni ikinci çağrıda aynen duruyor; salt-okunur dizinde istisna fırlatmıyor.

### Faz 3 — `src/project_docs.py`: `agent.md`
- Hedef: şablon + `ensure_project_docs()` (md5/sha bölüm hash'i, `preserve_meta`, `safe_write_file`,
  `redact_text`, kip kapısı, R1 ad çözümlemesi, `docs_state.json`).
- Kontrol: `tests/test_project_docs.py` — (a) boş tmp dizinde dosya oluşur; (b) elle eklenen
  "## Notlar" metni 2. çağrıda korunur; (c) ortam değişmeyince dosya mtime değişmez;
  (d) `AGENTS.md` varken `AGENTS.md` içeriği değişmez; (e) sahte API anahtarı içeren goal
  dosyaya maskeleli yazılır.

### Faz 4 — `changelog.md`
- Hedef: `append_changelog_entry()` + imza dedup + `recent` blok yeniden yazımı + 1200 kr sınırı;
  `run_step` içinde hafif `ToolTrace` toplayıcı (mevcut `print_tool_call` yanına, davranış değiştirmeden).
- Kontrol: test — aynı görev iki kez çalıştırılınca **tek** girdi; farklı görev → yeni girdi;
  eski girdiler silinmemiş; `plan` modunda hiç yazılmıyor.

### Faz 5 — Bağlam enjeksiyonu (prompt öncesi akıllı adım)
- Hedef: E4 (`_compose_system`), E6 (`request_frame_guidance` + `docs_digest`), token bütçesi ve
  kırpma sırası; `agent.md` kuralları ile sistem prompt'u çelişirse çözüm kuralı metni.
- Kontrol: `tests/test_docs_context.py` — sahte `agent.md` ile system prompt'ta bölüm başlıkları var;
  `EVREN_DOCS_TOKENS=0` iken blok yok; bütçe aşımında kırpma notu var; `refresh_context()`
  dosya değişimini yansıtıyor.

### Faz 6 — Otomatik tetikleme + CLI/REPL yüzeyi
- Hedef: E7/E8/E9 (açılış + görev sonu), E10 `evren docs [status|refresh|unmark|path]`,
  E11 `/docs`, E13 `.gitignore` satırı; banner'a tek satır `agent.md hazır` bilgisi.
- Kontrol: `python -m evren.cli docs status` (veya kurulu `evren docs status`) tmp dizinde doğru
  raporu veriyor; `/docs` REPL'de akışta; `docs unmark` **onay soruyor** ve blok dışını koruyor.

### Faz 7 — (Opsiyonel) Ajan araçları
- Hedef: `update_agent_doc(section, body)` ve `log_change(text)` araçları; `tool_gateway`de
  `plan` modunda yasak, `ask` modunda yasak; `PROTECTED_FILES` kontrolü.
- Kontrol: `tests/test_tools.py` + `tests/test_tool_gateway.py` genişletmesi; kip ihlalinde
  "REDDEDİLDİ" metni dönüyor.

### Faz 8 — Dokümantasyon, tam doğrulama, geri alma tatbikatı
- Hedef: `README.md`'ye "Akıllı Bağlam Dosyaları" bölümü; `AGENTS.md`'ye yönetilen-blok uyarısı;
  `CHANGELOG.md`'ye depo girişi; `scripts/bump_version.py` gerekip gerekmediğine karar.
- Kontrol (tam paket, cmd):
  ```
  python -m compileall src
  python -m pytest -q
  python -m pytest tests/test_docs_merge.py tests/test_cli_home.py tests/test_project_docs.py tests/test_docs_context.py -q
  evren --version
  evren docs status
  ```
- Geri alma tatbikatı: geçici dizinde `agent.md` oluştur → elle not ekle → `evren rollback agent.md`
  → notun ve yönetilen bloğun beklenen durumda olduğunu doğrula; `EVREN_AUTO_DOCS=false` ile
  özelliğin tamamen sustuğunu doğrula.

Sıra bağımlılığı: 0 → 1 → {2,3} → 4 → 5 → 6 → {7,8}. Faz 2 ve 3 paralel olabilir; Faz 5, 3 ve 2'ye
bağımlı; Faz 6, 3–5'e bağımlı.

---

## 9. Kabul Kriterleri (bitti ölçütü)

1. Boş bir dizinde `evren` açılınca `agent.md` + `changelog.md` oluşuyor; CLI ana dizininde
   (`EVREN_CLI_HOME` veya depo kökü) `skills.md`, `lessons.md`, `commands.md` oluşuyor.
2. Aynı dizinde ikinci açılışta **dosya içeriği değişmiyor** (hash idempotensi) — `git status` temiz.
3. Kullanıcının `agent.md` içine yazdığı serbest metin ve `skills.md` içindeki `## Özel Beceriler`
   hiçbir otomatik çalıştırmada kaybolmuyor.
4. Prompt hazırlama (netleştirme/öneri) ve ajan sistem prompt'u `agent.md` kurallarını ve son
   changelog girdilerini içeriyor; `/tokens` raporunda docs bağlamı ~3500 token'ı aşmıyor.
5. `plan` ve `ask` modlarında hiçbir docs dosyası yazılmıyor.
6. Docs kaynaklı hiçbir istisna CLI'yi çökertmiyor (salt-okunur dizin, uzun yol, kilit çakışması).
7. API anahtarı/parola içeren metin docs'a maskeleli yazılıyor; `python -m pytest -q` tam paket yeşil.
8. `evren rollback agent.md` ve `EVREN_AUTO_DOCS=false` ile özellik tek adımda geri alınabiliyor.
