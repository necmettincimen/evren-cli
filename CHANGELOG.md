# Değişiklik Günlüğü (Changelog)

Bu projedeki tüm önemli değişiklikler bu dosyada belgelenir.

Format [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) standardına,
sürümleme ise [Semantic Versioning](https://semver.org/lang/tr/) (SemVer) kurallarına dayanır.

## [0.10.0] - 2026-10-09

### Eklendi
- **Canlı komut çıktısı (streaming):** `run_command` aracı ve REPL `/run` komutu artık çıktıyı satır satır, üretildiği anda gösterir (stdout normal, stderr soluk kırmızı). Uzun süren test/derleme komutlarında ilerleme anbean izlenir.
- Yeni `stream_with_tree_kill()` (`src/proc_utils.py`): stdout/stderr'i ayrı okuyucu thread'lerle canlı akıtır; timeout'ta tüm süreç ağacını yine sonlandırır ve tam çıktı + çıkış kodunu döndürür.
- Yeni `print_command_output()` (`src/ui.py`): komut çıktısı satırlarını akışa uygun biçimde renklendirir.
- `tests/test_proc_utils.py`: canlı akış ve timeout davranışı için yeni testler.

### Düzeltildi
- **API anahtarı yoksa çökme giderildi:** `EVREN_API_KEY` bulunamadığında `ValueError` fırlatmak yerine kullanıcıdan interaktif olarak anahtar sorulur ve `~/.evren-cli/config.json` dosyasına kaydedilir; diğer ayarlar (base_url, model, max_tokens, temperature) varsayılan değerlerine düşer.

## [0.9.0] - 2026-10-08

### Eklendi
- **Arıza triyajları (diagnostics):** `/diagnose <ad>` ile derleme hatası, kararsız test, performans gerilemesi, bağımlılık çıkmazı, güvenlik açığı ve dağıtım hatası için yapılandırılmış triyaj; belirtiye göre otomatik eşleştirme.
- **Çıktı şablonları (templates):** `/template <ad>` ile olay sonrası analiz (postmortem), ADR, PR açıklaması, sürüm notları, runbook ve hata raporu iskeletleri.
- Yeni modüller: `src/diagnostics.py`, `src/templates.py` ve ilgili testler.

## [0.8.0] - 2026-10-08

### Eklendi
- **Beceri kataloğu (skills):** `/skills` ve `/apply <beceri>` ile kök neden hata ayıklama, cerrahi refactor, TDD, API entegrasyonu, güvenlik incelemesi, performans, kod incelemesi ve bağımlılık güncelleme gibi yeniden kullanılabilir mühendislik reçeteleri.
- **Kalıcı proje hafızası:** `.evren/memory.json` altında profil (stack, dil, konvansiyonlar, hedef) ve notlar; `/memory`, `/memory set`, `/memory note`. Hafıza her oturumda sistem istemine enjekte edilir.
- **Uygulama takibi:** `.evren/tasks.json` altında altı durumlu (not-started → in-progress → blocked → completed → abandoned → deferred) görevler; `/track`, `/task add`, `/progress`.
- **Playbook'lar:** `/playbook` ve `/resume` ile çok adımlı reçeteler (release, hotfix, onboarding, migration) ve `.evren/playbook.json` checkpoint'i.
- **Dil modu:** `/language <tr|en|de|...>` ile çıktı dili; kod İngilizce kalır, düzyazı seçilen dilde üretilir.
- Yeni modüller: `src/skills.py`, `src/memory.py`, `src/tracking.py`, `src/playbooks.py`, `src/language.py` ve ilgili testler.

## [0.7.0] - 2026-10-08

### Eklendi
- Kalıp sorularında önceki turun cevabı varsayılan olur: her alan önceki değeri prompt'ta gösterir, boş Enter ile kabul edilir.
- `collect_prompt_frame` artık `previous` parametresi alır; boş yanıt önceki turun değerini korur.

## [0.6.1] - 2026-10-07

### Değişti
- Sticky API anahtarı: /keys next ile seçilen anahtar bir sonraki next/failover'a kadar sabit kalır
- /keys add|remove anahtarları ~/.evren-cli/config.json ApiKeys alanına kalıcı yazar; startup'ta tüm ApiKeys yüklenir
- /quota tüm anahtarlar için kalan/limit/kullanılan/reset bilgisini gösterir
- /terms sessiz kalma ve spinner kaynaklı onay sorunu giderildi; havuzdaki tüm anahtarlar için şart kabulü

## [0.6.0] - 2026-10-07

### Eklendi
- Zorunlu netleştirme (clarify) adımı: ajan her promptta işe başlamadan önce belirsizlikleri `ask_user` ile kullanıcıya sorar; tahmin/varsayım minimize edilir.
- `ask_user` aracı açıklaması clarify amacını vurgulayacak şekilde güncellendi.
- `AGENTS.md` eklendi: ajan davranış kuralları (clarify akışı, çalışma ilkeleri, öncelik sırası).

## [0.5.0] - 2026-10-07

### Eklendi
- Kendi kendini güncelleme: `/update` komutu ve `evren update` alt komutu ile en son sürüm GitHub deposundan (`necmettincimen/evren-cli`) çekilir ve CLI yeni kodla otomatik yeniden başlatılır.
- Git checkout tespiti: kurulum bir git çalışma kopyasıysa `git pull --ff-only` + editable kurulum tazeleme; aksi halde `pip install --upgrade git+https://...` kullanılır.
- Güncelleme adımları `.evren/logs/update.log` dosyasına kaydedilir.

## [0.4.0] - 2026-10-07

### Değişti
- Günlük kota tükendiğinde (429 + quota işaretçisi) anahtar otomatik devre dışı bırakılır ve sıradaki API anahtarına geçilir (failover).
- Yeni QuotaExhaustedError tipi: geçici rate-limit'ten ayrıştırılır; aynı anahtarla tekrar denenmez, anahtar değiştirilir.
- chat_complete, chat_stream ve chat_stream_assembled akış başlamadan önce kota/auth hatalarında anahtar değiştirir; akış başladıktan sonra kısmi yanıt tekrarlanmaz.

## [0.3.0] - 2026-10-06

### Eklendi
- Reasoning (düşünme) yoğunluğu kontrolü: `EVREN_REASONING_EFFORT` ayarı (varsayılan: `low`) — daha hızlı yanıt ve koda daha çok token.
- REPL `/effort` komutu: çalışma zamanında `low | medium | high | none` seçimi.
- Sunucu `reasoning_effort` parametresini desteklemezse otomatik geri dönüş (istek kırılmaz).
- Ajan döngüsü artık **streaming** kullanıyor: düşünme ve yanıt token'ları terminalde canlı akar, "donmuş" hissi ortadan kalkar (`chat_stream_assembled`).

### Değişti
- Sistem promptu hız ve verimlilik için güncellendi: daha çok kod yaz, daha az düşün.
- Varsayılan `max_tokens` 4096 → 8192 (daha çok kod üretimi).
- Ajan maksimum adım sayısı 15 → 30 (daha uzun otonom görevler).

## [0.2.0] - 2026-10-05

### Eklendi
- SSH ile uzak sunucu işlemleri: kayıtlı host alias'ları üzerinden komut çalıştırma, dosya okuma/yazma.
- Yeni ajan araçları: `ssh_list_hosts`, `ssh_run`, `ssh_read_file`, `ssh_write_file`.
- SSH host kayıt defteri (`~/.evren/ssh_hosts.json` veya `<workspace>/.evren/ssh_hosts.json`); host başına `allow_write` bayrağı (varsayılan: salt-okuma).
- REPL `/ssh` komutları (list / add / remove / test) ve `evren ssh` alt komutu (list / add / remove / test / run).
- Uzak işlemler için denetim günlüğü (`.evren/logs/ssh_audit.log`).

### Değişti
- Mod geçidi SSH araçlarını destekler: `ssh_read_file`/`ssh_list_hosts` salt-okuma, `ssh_write_file` yazma, `ssh_run` ask/plan modunda salt-okuma komutlarıyla sınırlı.
- Salt-okuma komut listesine uzak durum tespiti komutları eklendi (`uptime`, `df`, `free`, `ps`, `journalctl` vb.).

## [0.1.2] - 2026-10-05

### Değişti
- (Bu sürümdeki değişiklikleri buraya yazın.)

## [0.1.1] - 2026-10-05

### Değişti
- SSL/TLS dogrulama hatasi duzeltildi: EVREN_SSL_VERIFY ve EVREN_CA_BUNDLE ayarlari eklendi (self-signed sertifika destegi).

## [0.1.0] - 2026-10-04

### Eklendi
- Antigravity tarzı interaktif TUI (REPL) modu ve scriptlenebilir alt komutlar.
- EVREN LLM API istemcisi: sohbet, akışlı (streaming) yanıt ve akıl yürütme (reasoning) desteği.
- Otonom çok adımlı ajan oturumu (`agent` komutu).
- API anahtar havuzu yönetimi (`/keys`), kota sorgulama (`/quota`) ve model listeleme (`/models`).
- Kullanım şartları doğrulama ve kabul akışı (`/terms`).
- Çalışma alanı tarama, dosya ağacı ve istatistik çıkarımı (`/scan`).
- Cerrahi dosya düzenleme, otomatik yedekleme ve geri alma (`/undo`, `rollback`).
- Çalışma kipleri: `normal`, `ask` (salt-okuma), `plan` (yalnızca plan yazımı).
- Bağlam/token bütçesi yönetimi ve geçmiş sıkıştırma.
- `--version` / `-V` bayrağı ve tek kaynaklı sürüm yönetimi (`src/version.py`).
- Açılış banner'ında sürüm ve son sürüm notlarının gösterimi.
