# EVREN CLI (Antigravity-Style AI Development Assistant)

> **EVREN LLM API** (`https://evren-llmapi.ssyz.org.tr/v1`) altyapısını kullanarak Windows üzerinde çalışan, **Google Antigravity CLI (`agy`)** mimarisinden esinlenilmiş açık kaynaklı otonom yazılım geliştirme ve proje düzenleme asistanı.

---

## 🌟 Öne Çıkan Özellikler

1. **Antigravity TUI ve Slash Komutları (`/`):**
   - `/help`, `/version`, `/model`, `/models`, `/quota`, `/terms`, `/scan`, `/files`, `/add`, `/drop`, `/undo`, `/run`, `/update`, `/clear`, `/exit`.
   - Zengin terminal arayüzü (`rich` panelleri, sözdizimi vurgulamalı markdown, diff tabloları).

2. **Sürüm Yönetimi ve Sürüm Notları:**
   - Tek kaynaklı sürüm (`src/version.py`), `pyproject.toml`'dan otomatik okunur.
   - `evren --version` / `-V` bayrağı ve `/version` slash komutu.
   - Açılış banner'ında sürüm numarası ve `CHANGELOG.md`'den çekilen sürüm notları gösterilir.

3. **Otonom Ajan Döngüsü (Agentic Loop):**
   - Kullanıcının verdiği çok adımlı geliştirme veya hata ayıklama görevini kendi başına analiz eder.
   - Dosyaları `view_file` ile okur, `edit_file` ile cerrahi blok değişiklikleri yapar, `write_file` ile yeni dosyalar üretir, `run_command` ile testleri çalıştırır.

4. **EVREN LLM API Entegrasyonu:**
   - OpenAI SDK ve HTTP/SSE uyumluluğu.
   - **Kullanım Şartları (Terms):** İlk çalıştırmada `/v1/terms/status` kontrolü ve etkileşimli şart onaylama akışı.
   - **Keep-Alive Filtreleme:** Sunucunun 10 saniyede bir gönderdiği `: keep-alive` SSE satırlarını filtreler.
   - **Düşünme (Reasoning) Desteği:** `deepseek-v4.1-flash`, `glm-5.3` ve `qwen` modellerinin `reasoning` / `reasoning_content` zincirini terminalde ayrı renkle canlı akıtır.
   - **Kota ve Token Takibi:** `x-evren-daily-remaining-tokens` başlığı ve `/v1/quota` uç noktası üzerinden kalan kotayı anlık izler.

5. **Windows Geliştirici Güvenliği:**
   - **Dizin Sınırı (Boundary Check):** Asistan yalnızca belirlenen çalışma alanı kökü içinde dosya okur ve yazar; dışarı sızamaz.
   - **Otomatik Yedekleme:** Değiştirilen her dosya `.evren/backups/` altında zaman damgasıyla yedeklenir.
   - **Rollback Desteği:** `/undo <dosya>` veya `evren rollback <dosya>` ile tek tuşla önceki sürüme dönülür.
   - **Renkli Diff Önizleme:** Değişiklikler uygulanmadan önce yeşil `+` ve kırmızı `-` satırlarıyla kullanıcıya gösterilir ve onay istenir.

---

## 🚀 Hızlı Başlangıç (Windows PowerShell)

### 1. Kurulum Betiğini Çalıştırın

```powershell
cd C:\Users\msi\.gemini\antigravity\scratch\evren-cli
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Veya elle kurmak isterseniz:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
copy .env.example .env
```

### 2. `.env` Dosyanızı Düzenleyin

```env
EVREN_API_KEY=evren_llm_gercek_anahtariniz_buraya
EVREN_BASE_URL=https://evren-llmapi.ssyz.org.tr/v1
EVREN_DEFAULT_MODEL=deepseek-v4.1-flash
EVREN_DEFAULT_MAX_TOKENS=4096
```

### 3. Asistanı Başlatın

```powershell
.\.venv\Scripts\Activate.ps1
evren
```

---

## 🌍 Sistem Geneli (Global) Kurulum — Her Dizinden `evren`

Proje klasörüne girmeden, **terminali hangi dizinde açarsanız açın** sadece `evren`
yazarak o dizinde otonom ajanı başlatmak için global kurulum betiğini kullanın.

### Tek Komutla Global Kurulum

```powershell
cd C:\Users\msi\.gemini\antigravity\scratch\evren-cli
powershell -ExecutionPolicy Bypass -File .\scripts\install-global.ps1
```

Bu betik şunları yapar:

1. Python sürümünü doğrular.
2. `evren-cli` paketini **global Python ortamına** (editable) kurar → `evren` komutu oluşur.
3. Python `Scripts` klasörünü kullanıcı `PATH`'ine ekler (gerekiyorsa).
4. `.env` dosyası yoksa `.env.example`'dan oluşturur.
5. Komutun erişilebilir olduğunu doğrular.

> **Not:** PATH değişikliğinin geçerli olması için kurulumdan sonra **yeni bir terminal**
> açmanız gerekir.

### Global Kurulumu Test Etme

Yeni bir terminal açın ve herhangi bir proje dizinine gidip çalıştırın:

```powershell
cd C:\Projeler\BenimProjem
evren
```

Ajan, **bulunduğunuz dizini otomatik olarak çalışma alanı** kabul eder ve o dizinde
çalışmaya başlar. Farklı bir dizini hedeflemek isterseniz:

```powershell
evren --workspace C:\Baska\Dizin
```

### Global Kurulumu Kaldırma

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-global.ps1 -Uninstall
```

### Alternatif: Sanal Ortama Global Kurulum

Global Python ortamını kirletmek istemezseniz, komutu proje `.venv`'ine kurup
`.venv\Scripts` klasörünü PATH'e ekleyebilirsiniz:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-global.ps1 -Venv
```

---

## 💻 Kullanım Biçimleri

### A. Etkileşimli Antigravity TUI Modu

Herhangi bir argüman vermeden çalıştırdığınızda doğrudan etkileşimli terminal asistanı açılır:

```powershell
evren
```

```text
═════════════════════════════════════════════════════════════════
✦ EVREN CLI v0.1.0 • Antigravity-Style AI Development Assistant
Model: deepseek-v4.1-flash   Çalışma Alanı: C:\Projeler\MyProject
Kalan Günlük Kota: 980,240

v0.1.0 Sürüm Notları:
  • Antigravity tarzı interaktif TUI (REPL) modu ve scriptlenebilir alt komutlar.
  • EVREN LLM API istemcisi: sohbet, akışlı (streaming) yanıt ve akıl yürütme desteği.
═════════════════════════════════════════════════════════════════

evren [deepseek-v4.1-flash] > /scan
evren [deepseek-v4.1-flash] > src/api_client.py dosyasını incele ve timeout süresini artır
evren [deepseek-v4.1-flash] > pytest ile testleri çalıştır
```

### B. Tek Seferlik Komut Satırı Modu

```powershell
# Hızlı soru-cevap ve streaming
evren chat "FastAPI ile asenkron endpoint nasıl tanımlanır?" --stream

# Belirli bir dosya bağlamıyla analiz
evren chat "Bu kodda güvenlik açığı var mı?" -f src/api_client.py

# Tek komutla otonom ajan görevi
evren agent "tests/ klasöründeki testleri çalıştır ve eksik testleri ekle"

# Beş satırlık kalıp: Rol/Çerçeve system'e, Brief/Kısıt/Çıktı user'a gider
evren agent --rol "kıdemli Windows mühendisi" --cerceve "cerrahi yama, mevcut stili koru" --brief "timeout süresini 45 saniyeye çıkar" --kisit "yalnız src/api_client.py" --cikti "çalışan kod ve pytest"

# Çalışma alanı dosya ağacı
evren scan

# Kalan token kotası
evren quota

# Kullanım şartlarını denetle/onayla
evren terms

# Değiştirilen dosyayı geri yükle
evren rollback src/api_client.py

# Sürüm bilgisini göster
evren --version
```

---

## 🛠️ Slash Komutları Tablosu

| Komut | Açıklama |
|---|---|
| `/help` | Kullanılabilir tüm slash komutlarını ve ipuçlarını listeler |
| `/version` | Sürüm numarasını ve `CHANGELOG.md`'den çekilen sürüm notlarını gösterir |
| `/model <ad>` | Aktif modeli anında değiştirir (örn: `/model glm-5.3`) |
| `/models` | EVREN API'deki tüm modelleri dinamik olarak çeker |
| `/quota` | Günlük kalan token kotasını gösterir |
| `/terms` | Kullanım şartlarının durumunu denetler ve onaylatır |
| `/skills [kategori]` | Kullanılabilir mühendislik becerilerini listeler |
| `/apply <beceri>` | Bir beceriyi mevcut göreve uygular (örn: `/apply root-cause-debug`) |
| `/diagnose [ad]` | Arıza triyajı çalıştırır (örn: `/diagnose build-failure`) |
| `/template [ad]` | Çıktı şablonunu doldurur (örn: `/template adr`) |
| `/memory` | Proje hafızasını (profil + notlar) gösterir |
| `/memory set <k> <v>` | Profil alanı kaydeder (örn: `/memory set stack FastAPI`) |
| `/memory note <metin>` | Hafızaya not ekler |
| `/track [filtre]` | Görevleri listeler (all\|open\|<durum>) |
| `/task add <id> <açıklama>` | Yeni takip görevi ekler |
| `/progress <id> <yüzde>` | Görev ilerlemesini günceller |
| `/playbook [ad]` | Playbook'ları listeler veya başlatır |
| `/resume` | Kaydedilmiş playbook adımından devam eder |
| `/language [ad]` | Çıktı dilini ayarlar (örn: `/language tr`) |
| `/scan [yol]` | Çalışma alanını tarar, ASCII ağaç ve uzantı analizi sunar |
| `/files` | İstem bağlamına eklenmiş dosyaları listeler |
| `/add <dosya>` | Bir dosyayı aktif bağlama ekler |
| `/drop <dosya>` | Bir dosyayı aktif bağlamdan çıkarır |
| `/undo <dosya>` | `.evren/backups` klasöründeki son yedeğe geri döner |
| `/run <komut>` | Onay alarak PowerShell terminal komutu çalıştırır |
| `/ssh` | Kayıtlı SSH host'larını listeler |
| `/ssh add <alias> <user@host> [port]` | Yeni bir SSH host'u tanımlar |
| `/ssh remove <alias>` | Kayıtlı SSH host'unu siler |
| `/ssh test <alias>` | SSH bağlantısını test eder |
| `/update` | En son sürümü GitHub'dan çeker ve CLI'ı yeniden başlatır |
| `/clear` | Sohbet ve bağlam geçmişini sıfırlar |
| `/exit` | Uygulamadan güvenli şekilde çıkar |

---

## 🔐 SSH ile Uzak Sunucu İşlemleri

EVREN CLI, kayıtlı uzak sunucularda (host alias'ları üzerinden) komut çalıştırabilir,
dosya okuyabilir ve yazabilir. Güvenlik için **yalnızca önceden tanımlı alias'lara**
bağlanılır; rastgele host/IP kullanılamaz.

### Host Tanımlama

Host'lar `~/.evren/ssh_hosts.json` (veya çalışma alanındaki `.evren/ssh_hosts.json`)
dosyasında tutulur:

```json
{
  "prod-web": {
    "host": "10.0.0.5",
    "user": "deploy",
    "port": 22,
    "identity_file": "C:\\Users\\me\\.ssh\\id_ed25519",
    "description": "Üretim web sunucusu",
    "allow_write": false
  }
}
```

> `allow_write` varsayılan olarak `false`'tur; uzak yazma host başına bilinçli olarak açılır.

### REPL İçinden

```text
evren [deepseek-v4.1-flash] > /ssh add prod-web deploy@10.0.0.5 22
evren [deepseek-v4.1-flash] > /ssh test prod-web
evren [deepseek-v4.1-flash] > prod-web sunucusunda nginx durumunu kontrol et
```

### Komut Satırından

```powershell
evren ssh list
evren ssh add prod-web deploy@10.0.0.5 --port 22
evren ssh test prod-web
evren ssh run prod-web "systemctl status nginx"
```

### Güvenlik Modeli

- **Yalnızca alias'lı host'lar:** Rastgele hedefe bağlanma engellenir.
- **Anahtar/agent tabanlı auth:** `BatchMode=yes` ile parola prompt'unda takılma olmaz.
- **Mod geçidi:** `ssh_read_file`/`ssh_list_hosts` salt-okuma (her modda); `ssh_write_file` yazma (ask/plan'da reddedilir); `ssh_run` ask/plan'da yalnızca salt-okuma komutlarıyla sınırlı.
- **Onay akışı:** Her uzak komut/yazma öncesi kullanıcıdan onay istenir (`EVREN_AUTO_APPROVE=false` iken).
- **Denetim günlüğü:** Tüm uzak işlemler `.evren/logs/ssh_audit.log` dosyasına kaydedilir.
- **Injection koruması:** Uzak yazma içeriği stdin üzerinden gönderilir; shell tarafından yorumlanmaz.

> **Not:** Windows'ta `ssh` istemcisinin kurulu olması gerekir (Ayarlar → Uygulamalar →
> İsteğe Bağlı Özellikler → OpenSSH Client).

---

## 🧩 Beceriler, Hafıza, Takip ve Playbook'lar

Foreman'dan uyarlanan dört çekirdek yetenek, ajanı tek seferlik bir sohbetten
kalıcı bir geliştirme ortağına dönüştürür:

- **Beceriler (`/skills`, `/apply`):** Kök neden hata ayıklama, cerrahi refactor,
  TDD, API entegrasyonu, güvenlik incelemesi, performans, kod incelemesi ve
  bağımlılık güncelleme gibi yeniden kullanılabilir mühendislik reçeteleri.
  `/apply root-cause-debug` gibi bir komut, ilgili adımları ajan turuna enjekte eder.
- **Proje hafızası (`/memory`):** `.evren/memory.json` altında profil (stack, dil,
  konvansiyonlar, hedef) ve notlar saklanır; her oturumda sistem istemine eklenir.
- **Uygulama takibi (`/track`, `/task`, `/progress`):** `.evren/tasks.json` altında
  altı durumlu görevler; öneri ile gerçekleşen iş arasındaki boşluğu kapatır.
- **Playbook'lar (`/playbook`, `/resume`):** Release, hotfix, onboarding ve migration
  gibi çok adımlı reçeteler; `.evren/playbook.json` checkpoint'i ile kaldığı yerden devam.
- **Dil modu (`/language`):** Kod İngilizce kalır, kullanıcıya dönük düzyazı seçilen
  dilde üretilir (örn: `/language tr`).
- **Arıza triyajları (`/diagnose`):** Derleme hatası, kararsız test, performans
  gerilemesi, bağımlılık çıkmazı, güvenlik açığı ve dağıtım hatası için
  yapılandırılmış triyaj; belirtiye göre otomatik eşleştirme.
- **Çıktı şablonları (`/template`):** Postmortem, ADR, PR açıklaması, sürüm notları,
  runbook ve hata raporu iskeletleri; ajan bunları eksiksiz doldurur.

---

## 🧠 EVREN LLM Model Rehberi

| Model | Tavsiye Edilen Kullanım Alanı | max_tokens |
|---|---|---|
| `deepseek-v4.1-flash` | **Ana Kodlama ve Ajan Modeli** (1M bağlam, güçlü kod yazımı) | 4096 (min 2048) |
| `glm-5.3` | **Derin Akıl Yürütme ve Çok Adımlı Analiz** | 4096 (min 2048) |
| `qwen3.8-flash-next` | **Hızlı Yanıtlar, Vibe Coding, Sohbet** | 4096 |
| `auto` | Otomatik model yönlendirme | 4096 |
| `dots-ocr` / `deepseek-ocr-2` | Belge, taranmış fatura ve görsel OCR | - |
| `qwen3-asr-1.7b` | Ses kayıtlarını metne çevirme (ASR) | - |
| `qwen3-embedding-8b` | Vektör gömme ve semantik arama | - |
| `qwen3-guard-4b` | Güvenlik ve zararlı istek denetimi | 1024 |

---

## 📁 Proje Dosya Yapısı

```text
evren-cli/
├── .env.example              # Örnek çevre değişkenleri
├── .gitignore                # Güvenlik ve önbellek yoksayma kuralları
├── CHANGELOG.md              # Sürüm notları (Keep a Changelog formatı)
├── README.md                 # Kapsamlı kullanım dokümantasyonu
├── pyproject.toml            # Python paket ve entrypoint tanımı ('evren')
├── requirements.txt          # Gerekli kütüphaneler (openai, httpx, rich, prompt_toolkit)
│
├── src/
│   ├── __init__.py           # Paket başlatıcı
│   ├── version.py            # Tek kaynaklı sürüm yönetimi ve changelog okuyucu
│   ├── cli.py                # Ana CLI uç noktası ve argüman ayrıştırıcı
│   ├── config.py             # .env ve ayar yönetimi, çalışma alanı dizinleri
│   ├── api_client.py         # EVREN LLM API istemcisi (OpenAI SDK + streaming + keep-alive + retry)
│   ├── terms.py              # Kullanım şartları doğrulama ve kabul döngüsü
│   ├── agent.py              # Antigravity otonom ajan döngüsü ve adım yönetimi
│   ├── tools.py              # Ajan araçları (view_file, edit_file, write_file, run_command vb.)
│   ├── project_scanner.py    # Dosya tarama, ASCII ağaç ve uzantı istatistikleri
│   ├── file_ops.py           # Güvenli dosya I/O, sınır denetimi, otomatik yedekleme ve rollback
│   ├── diff_viewer.py        # Unified diff üretici ve cerrahi metin değiştirici
│   ├── skills.py             # Mühendislik beceri kataloğu (reçeteler)
│   ├── diagnostics.py        # Arıza triyajları (belirti → tanı → yönlendirme)
│   ├── templates.py          # Çıktı şablonları (postmortem, ADR, PR, runbook)
│   ├── memory.py             # Kalıcı proje hafızası (profil + notlar)
│   ├── tracking.py           # Uygulama takibi (altı durumlu görevler)
│   ├── playbooks.py          # Çok adımlı reçeteler ve checkpoint
│   ├── language.py           # Çıktı dil modu
│   ├── ssh_hosts.py          # SSH host kayıt defteri (alias tabanlı, allow_write bayrağı)
│   ├── ssh_ops.py            # Uzak SSH işlemleri (komut çalıştırma, dosya okuma/yazma, audit log)
│   ├── repl.py               # Etkileşimli TUI kabuğu ve slash komut yöneticisi
│   └── ui.py                 # Antigravity tarzı Rich terminal formatları, paneller ve diff boyama
│
├── tests/
│   ├── test_file_ops.py      # Dosya sınırları, yazma, yedekleme ve rollback testleri
│   ├── test_diff_viewer.py   # Diff üretimi ve cerrahi blok değiştirme testleri
│   ├── test_ssh_ops.py       # SSH host kaydı, komut oluşturma, yazma koruması ve mod geçidi testleri
│   └── test_tools.py         # Ajan araçları ve işlev çağrısı testleri
│
└── scripts/
    ├── install.ps1           # Windows PowerShell otomatik tek tık kurulum betiği (yerel .venv)
    ├── install-global.ps1    # Sistem geneli kurulum: her dizinden 'evren' komutu
    └── bump_version.py       # Sürüm artırma betiği (pyproject + version.py + CHANGELOG)
```

---

## ⚙️ Yapılandırma ve C# CLI ile Uyumluluk Notu

Bu Python sürümü, C# `evren-cli` ile **aynı** `~/.evren-cli/config.json` dosyasını
okur ve `/keys add` / `/keys remove` ile `ApiKeys` alanına yazar (`BaseUrl` ve
diğer alanlar korunur). Ortam değişkenleri (`EVREN_API_KEY` / `EVREN_API_KEYS`)
varsa onlar önceliklidir; yoksa config’teki tüm anahtarlar havuza yüklenir.

> Çoklu API anahtarı için `EVREN_API_KEYS` (virgülle ayrılmış) veya config
> `ApiKeys` kullanılabilir; tek `EVREN_API_KEY` geriye dönük olarak çalışmaya
> devam eder. Aktif anahtar `/keys next` ile sabitlenir (otomatik round-robin yok;
> kota/auth failover yine sıradaki anahtara geçer).

---

## 🔖 Sürüm Yönetimi

Sürümün tek kaynağı `pyproject.toml`'dur; `src/version.py` bunu otomatik okur.
Sürümü artırmak için `scripts/bump_version.py` betiğini kullanın:

```powershell
# SemVer bileşenine göre artır (patch | minor | major)
python scripts/bump_version.py patch
python scripts/bump_version.py minor

# Belirli bir sürüme ayarla
python scripts/bump_version.py --set 1.2.3

# CHANGELOG notlarıyla birlikte artır
python scripts/bump_version.py minor --notes "Yeni özellik X" --notes "Hata düzeltmesi Y"

# Hiçbir dosyayı değiştirmeden önizle
python scripts/bump_version.py patch --dry-run
```

Betik şu üç dosyayı tutarlı biçimde günceller:

1. `pyproject.toml` → `[project] version`
2. `src/version.py` → `_FALLBACK_VERSION`
3. `CHANGELOG.md` → yeni sürüm başlığı ve notlar

---

## 🛡️ Güvenlik Politikası

1. **Asla Dışarı Taşmama:** Dosya işlemleri sadece projenin kendi kök klasöründe yapılabilir (`C:\Windows`, `..`, vb. engellenmiştir).
2. **Kritik Dosya Koruması:** `.env` ve sistem dosyalarının doğrudan ezilmesi önlenir.
3. **Kullanıcı Onayı:** `EVREN_AUTO_APPROVE=false` olduğu sürece her dosya değişikliği ve her PowerShell komutu öncesi kullanıcıdan `[Y/n]` onayı istenir.
4. **Geri Alınabilirlik:** Her değişikliğin orijinal hali `.evren/backups` klasörüne zaman damgasıyla saklanır.
