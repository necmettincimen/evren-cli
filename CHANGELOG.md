# Değişiklik Günlüğü (Changelog)

Bu projedeki tüm önemli değişiklikler bu dosyada belgelenir.

Format [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) standardına,
sürümleme ise [Semantic Versioning](https://semver.org/lang/tr/) (SemVer) kurallarına dayanır.

## [0.17.0] - 2026-10-12

### Düzeltildi
- Argo CD pod sorgusu artık olmayan `/pods`, `/resource/pods` ve `/resource/tree` yollarına gitmez. Kaynak ağacı `GET /api/v1/applications/{ad}/resource-tree` ile okunur; HTTP kodu 200 değilse gövde JSON sanılmaz.

## [0.16.0] - 2026-10-12

### Düzeltildi
- Kesik veya boş model yanıtı ikinci turda görevi düşürmez. `qwen3.8-flash-next` çıktı tabanı 16384 tokendır; boş yanıtta bütçe artar ve vazgeçmeden önce dört deneme yapılır. Yazılmış ama kesilmiş metin saklanır, tamamlanmış araç çağrısı uygulanır.
- Bağlantı, zaman aşımı ve 5xx hataları token gelmeden önce yeniden denenir. Ajan turu da bu hatalarda hemen bitmez.
- Ctrl+C çalışan komutu durdurur ve konuşma geçmişini silmez. Sonraki satır aynı göreve küçük bir düzeltme olarak eklenir; boş Enter kaldığı yerden devam eder.

## [0.15.0] - 2026-10-12

### Düzeltildi
- Model isteğinde adım talimatı artık konuşmanın sonuna ikinci bir system mesajı olarak eklenmez; Qwen'in "System message must be at the beginning" reddini önlemek için tüm system metinleri baştaki tek system mesajında birleşir.
- Prompt hazırlama, serbest metin yerine `submit_frame_guidance` araç JSON şemasını doldurur. Kesilen veya filtrelenen yanıtlar görevi başlatmaz.

## [0.14.0] - 2026-10-12

### Değiştirildi
- Varsayılan model `qwen3.8-flash-next` olarak ayarlandı (düşük gecikme, yüksek eşzamanlılık); `EVREN_DEFAULT_MODEL` kod varsayılanı ve `.env` güncellendi, `RECOMMENDED_MODELS` listesinde öne alındı.

## [0.13.0] - 2026-10-12

### Değiştirildi
- SSL/TLS sertifika doğrulaması varsayılan olarak kapatıldı (`EVREN_SSL_VERIFY` varsayılanı `false`); self-signed/kurumsal (MITM) sertifikalar tüm HTTP isteklerini artık engellemez. Doğrulamayı geri açmak için `EVREN_SSL_VERIFY=true` ayarlanabilir.

## [0.12.3] - 2026-10-12

### D?zeltildi
- Windows pip kurulumlar?nda `evren update` / `/update`, ?al??an `evren.exe` kilitliyken (`WinError 32`) pip'i ayn? s?re?te ?al??t?rmaz; g?ncelleme EVREN kapand?ktan sonra ayr? bir pencerede, kilit kalkana kadar yeniden denenerek yap?l?r.

## [0.12.2] - 2026-10-12

### Düzeltildi
- Komut çıktısı, araç sonuçları ve hata mesajlarındaki `[/path]` gibi köşeli parantezli metinler Rich biçimlendirme etiketi sanılıp CLI'ı çökertmez; metin olduğu gibi gösterilir.
- Durum göstergesi, akıl yürütme akışı ve diff başlıkları da düz metin olarak işlenir; Kubernetes log satırıyla regresyon testleri eklendi.

## [0.12.1] - 2026-10-11

### Eklendi
- `/execute <yanıt>` mevcut görev geçmişine form hazırlamadan onay veya takip yanıtı gönderir.

### Düzeltildi
- Brief satırındaki "yap artık", "uygula" ve "onay veriyorum" gibi kısa takip yanıtları yeni görev sayılmaz; mevcut hedef ve konuşma geçmişi korunur.
- Netleştirme sırasında "yap artık", "soru sorma" veya "devam" yanıtı kalan form ve önerileri atlar; Brief, önceki yanıtlar ve çözülemeyen sorular korunarak salt-okuma keşfine geçilir.
- Ajan talimatları verilmiş onayların ve yanıtlanmış soruların tekrarını önler; salt-okuma keşfi netleştirme öncesinde de yapılabilir. SSH yazma izinleri, kip kısıtlamaları ve komut onayları korunur.
- Önceki görev bulunmadığında açık uyarı verilir; diskteki checkpoint silinmez. Takip yönlendirmesi, sıfır hazırlık API çağrısı ve izin sınırları için regresyon testleri eklendi.

## [0.12.0] - 2026-10-11

### Eklendi
- **Göreve devam:** Brief satırında `devam` veya `/continue`, adım sınırında duraklayan görevi formu tekrarlamadan yeni adım bütçesiyle sürdürür; geçmiş ve görev kipi korunur.
- **Onaylı kalıcı checkpoint:** Adım sınırında kayıt onayı; sonraki oturumda `/continue` ile onaylı geri yükleme. `/checkpoint [save|clear]` ile kayıt yönetimi. Yaygın kimlik bilgileri maskelenir; kayıt şifrelenmez ve hassas görevlerde reddedilebilir.
- **Engel takibi:** `/blockers`, onaylı engel kanıtlarını ve çözüm yönlendirmelerini gösterir; `add` ve `resolve` ile görev engelleri yönetilir.
- **Görev gözden geçirme:** `/check-in [update]`, açık, gecikmiş ve 14 gündür duraklamış görevleri gösterir; değişiklikler ayrı kullanıcı onayıyla kaydedilir.
- **Karar günlüğü:** `/decisions [add]`, karar, gerekçe ve riskleri onayla saklar; son 10 karar yürütme izni sayılmadan ajan bağlamına eklenir.

### Düzeltildi
- Asıl görev hedefi geçmiş sıkıştırmasından bağımsız korunur. Checkpoint yükleme eski araç çağrılarını otomatik yürütmez; devam sırasında canlı durumun doğrulanması istenir.
- Bağlam yenileme mevcut Rol/Çerçeve alanlarını korur; checkpoint biçimi, araç eşleşmeleri, parola maskeleme ve onay sınırları için regresyon testleri eklendi.

## [0.11.3] - 2026-10-11

### Eklendi
- Netleştirme soruları, alan önerileri ve karar uyarıları ayrı renkli panellerde; giriş etiketleri aynı renklerle vurgulanır.
- `EVREN_UI_THEME=light|dark` ile form renk paleti açıkça seçilir; varsayılan `dark`. Açık temada koyu mavi, koyu yeşil ve koyu turuncu kullanılır; terminal arka planı değiştirilmez.

### Düzeltildi
- Uzun metinlerin sarılması ve Rich olmadan düz metin gösterimi korunur. Paletlerin beyaz ve koyu referans arka planlarında en az 4.5:1 kontrastı, panel/giriş renk uyumu ve tema doğrulaması test edilir.

## [0.11.2] - 2026-10-11

### Düzeltildi
- Netleştirme sınırında yanıtların kaybolması önlendi: kullanıcı asıl hedefini düzeltebilir, mevcut bilgilerle salt-okuma keşfine geçebilir veya iptal edebilir.
- Aynı netleştirme sorusunun tekrarı yakalanır; çözülemeyen sorular ve son hedef açıklaması görevle birlikte aktarılır.
- Prompt hazırlığı asıl niyete ve tek karar sorularına öncelik verir; araçlarla keşfedilebilecek ayrıntılar için soru döngüsünden kaçınır. PostgreSQL aktif-aktif taleplerinde eşzamanlı yazma ve failover ayrımı vurgulanır.
- İki sunuculu senaryo, hedef düzeltme ve yanıtların korunması için regresyon testleri eklendi.

## [0.11.1] - 2026-10-11

### Düzeltildi
- Uzun netleştirme soruları ve alan önerileri giriş satırından ayrı, terminal genişliğine göre sarılarak gösterilir; metin kırpılmaz ve Rich işaretleri düz metin olarak korunur.
- `evren update` sonrasında yeniden başlatma `update` komutunu tekrar çalıştırmaz; güncelleme döngüsü önlendi. Çalışma dizini, çalışma alanı ve model seçenekleri korunur.
- Dar terminalde soru/öneri gösterimi ve güncellemenin yalnızca bir kez çalışması için regresyon testleri eklendi.

## [0.11.0] - 2026-10-11

### Eklendi
- **Brief odaklı prompt hazırlama:** Etkileşimli form önce Brief'i alır, gerçek belirsizlikleri tek tek netleştirir ve boş Rol, Çerçeve, Kısıt, Çıktı alanlarını önerir. Her öneri ayrı kabul edilebilir, düzenlenebilir veya boş bırakılabilir.
- Yerel işletim sistemi, komut kabuğu ve çalışma dizini ajan bağlamına eklenir.
- Prompt hazırlama, küçük adımlı yürütme ve kesilmiş yanıt davranışları için regresyon testleri.

### Değişti
- Yeni Brief önceki alanları temizler; Brief'te Enter önceki kalıbı korur. Netleştirme yanıtları görevle birlikte gönderilir; hazırlık hatalarında görev başlatılmaz.
- Karmaşık görevler 3-7 doğrulanabilir alt adıma yönlendirilir; her turda sıradaki küçük adım uygulanır.

### Düzeltildi
- “Bu host”, “bu hosts”, “bu makine” ve “localhost” için yerel araç kullanımı netleştirildi; SSH yalnızca açık uzak hedef istekleri için önerilir.
- Akış yanıtının bitiş nedeni korunur; token sınırında kesilen veya boş yanıt bir kez toparlanır. Tekrarlayan kesilme ve adım sınırı açık tamamlanmama hatası üretir.
- Kesilmiş/geçersiz araç argümanlarının çalıştırılması ve reddedilen kimlik alanlarının önceki kalıptan geri gelmesi önlendi.

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
