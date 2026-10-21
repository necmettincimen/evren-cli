# Değişiklik Günlüğü (Changelog)

Bu projedeki tüm önemli değişiklikler bu dosyada belgelenir.

Format [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/) standardına,
sürümleme ise [Semantic Versioning](https://semver.org/lang/tr/) (SemVer) kurallarına dayanır.

## [3.6.1] - 2026-10-21

### Eklendi
- plan `push`: push sonrası Drone build'i API'den tetiklenir (`DRONE_TOKEN` ortamdan, sunucu `DRONE_SERVER`); GitHub webhook gecikmesi/kaybı build'i engellemez. `"drone": false` ile kapatılır; hata push'u düşürmez. Not: API tetiği `event=custom`'dır, `.drone.yml` push pipeline'ı `custom`'ı da kabul etmelidir.

## [3.6.0] - 2026-10-21

### Eklendi
- plan `agent`: başsız yönerge güçlendirildi (analizle bitirme, aynı turda çoklu okuma/arama); `repo` git deposuysa ajan dosya değiştirmeden bitirince `retries` (varsayılan 1) kez uyarıyla yeniden dener, yine değişiklik yoksa adım hata verir (`require_changes: false` ile kapatılır).
- usage.jsonl: her API çağrısına `latency_ms` eklendi (adım süresi ölçümü).

### Düzeltildi
- plan `push`: sabit `main` yerine deponun geçerli dalına (ya da `branch` alanına) push eder; master dallı depolarda başarısız oluyordu.

### Değiştirildi
- view_file varsayılan penceresi 250→400 satır, azami 400→800 satır, 24k→40k karakter: büyük C# dosyaları için tur sayısı azalır.

## [3.5.8] - 2026-10-21

### Düzeltildi
- plan `commit`: mesaj stdin'den utf-8 verilir; Windows'ta Türkçe karakterli commit mesajları çift kodlanıyordu.

## [3.5.7] - 2026-10-21

### Eklendi
- plan: `~/.evren/secrets.env` (KEY=VALUE) otomatik yüklenir; sudo/API sırları komut satırına ve plana yazılmadan `${ENV}` / `sudo_pass_env` ile kullanılır.

## [3.5.6] - 2026-10-21

### Eklendi
- plan `ssh`/`scp`: `identity` (özel anahtar) ve `ssh` için `sudo_pass_env` (sudo parolası ortam değişkeninden, `sudo -S bash -c` ile; plana sır yazılmaz).

## [3.5.5] - 2026-10-21

### Düzeltildi
- plan `agent` adımı başsız `--yes` ile çalışır; yazma izni istemeden bırakıyordu.

## [3.5.4] - 2026-10-21

### Düzeltildi
- plan: alt süreç çıktısı Windows'ta cp1254 ile çözülürken çöküyordu (agent adımı `NoneType.strip`); tüm alt süreçler utf-8 + errors=replace okur.
- plan `run`: Git Bash varsa POSIX kabukla çalışır (`tail -1`, `||`, `&&` cmd.exe'de bozuluyordu); `"shell": "cmd"` ile eski davranış.
- plan `replace`: CRLF dosyalarda `
` eşleşir, `flags` ("s","i","m") desteği, hiçbir dosyada eşleşme yoksa hata verir (`"required": false` ile atlanır); düz metin `with` içinde ters eğik çizgi artık yorumlanmaz.

## [3.5.0] - 2026-10-20

### Eklendi
- `evren plan <plan.json>`: LLM'siz, refleks hızında plan yürütücü. Üst planlayıcının yazdığı JSON adım listesini (clone_tree, replace, write, run, commit, push, agent) deterministik çalıştırır; ilk hatada durur (`--from N` ile devam), `--dry-run`, push yalnız `--allow-push` ile, commit mesajında AI imzası reddedilir.
- `clone_tree` ajan aracı: dizini kopyalayıp dosya/klasör adlarında ve içerikte toplu yeniden adlandırır (şablon repodan yeni repo); sistem istemine kullanım ipucu eklendi.

## [3.4.1] - 2026-10-20

### Düzeltildi
- İterasyon: HANDOFF başlığı ve state.json daha ilk turdan önce yazılır; süreç sert öldürülse (SIGKILL/görev durdurma) bile kayıt kalır (atexit o durumda çalışmıyordu)

## [3.4.0] - 2026-10-20

EVREN'in kendi iterasyonunun (16 commit) ürettiği iterasyon-modu iyileştirmeleri + düzeltme:

### Eklendi
- Kesinti dayanıklılığı: Ctrl+C ya da süreç durmasında (atexit) kısmi tur özeti HANDOFF'a yazılır, işlenmemiş maddeler 'kısmi' işaretlenir; --resume sonrası özetsiz tur kalmaz
- Planlayıcı madde kuralları: tek komutla doğrulanabilir kabul ölçütü, en fazla 1 analiz maddesi; 'kısmi' maddeler sonraki turda 2 alt maddeye bölünür
- Varsayılan --item-steps 40; madde içinde aynı kontrol en fazla 2 kez

### Düzeltildi
- Planlayıcı ilk turda hiçbir iş yapmadan `done+reason` ile vazgeçerse HANDOFF artık "HEDEF TAMAM" değil "DURDU (planlayıcı)" yazar ve çıkış kodu 1 döner

## [3.3.1] - 2026-10-20

EVREN'in kendi yük testi iterasyonunun (3. tur) ürettiği düzeltmeler:

### Değişti
- Plan kipi: keşif 2 adımla sınırlı, plan tek çağrıda yazılır
- edit_file yanıtı diff özeti ve "doğrulama gerekmez" notu taşır (düzenleme sonrası gereksiz view_file/test turları azalır)
- Zaman aşımında çıkış kodu 124 ve net mesaj
- HTTP notu: çoklu curl denemesi tek döngüde, 3 ardışık 404'te tarama biter

## [3.3.0] - 2026-10-20

### Eklendi
- Aynı hata koruması: dosya değiştirildiği halde aynı test/komut çıktısı 4. kez gelince ajan geri alıp 'kısmi' bildirmeye yönlendirilir (zaman/pid gürültüsü hash'ten ayıklanır)
- Komut çıktısı 100 KB'yi aşarsa bağlama yalnızca boyut özeti girer (EVREN'in kendi yük testinde bulundu)

### Düzeltildi
- edit_file büyük dosyada kırpılmış okumayı tüm dosya olarak geri yazabiliyordu; artık kırpmasız okuma + yalnızca değişen aralık yazılır
- İterasyon: hedef "commit" istiyorsa madde talimatı commit'e izin verir; senaryo/TSV metinleri iş talebi sayılmaz
- is_shell_input src.commands'a taşındı (shell_mode yeniden dışa aktarır), eksik --check aracı için açık mesaj, zaman aşımı testi

## [3.2.3] - 2026-10-20

### Değişti
- Ajan doğrulamada yalnızca ilgili testi koşar; tam paket istenmedikçe çalıştırılmaz (yük testinde 2 senaryoda 467 test gereksiz koşuyordu)
- `evren iterate` erken durduğunda (kontrol aracı eksik, engel, ilerleme yok) çıkış kodu 1 döner

## [3.2.2] - 2026-10-20

### Düzeltildi
- İterasyon: planlayıcı "hedef karşılandı" nedeniyle `done` döndüğünde kontroller geçiyorsa başarı sayılır (engel olarak durmaz)

## [3.2.1] - 2026-10-20

### Düzeltildi
- İterasyon: `flutter` PATH'te yoksa tam yol çözülür (D:/flutter vb.); kontrol aracı bulunamazsa (çıkış 127) tur tur boşa dönmek yerine hemen durur
- İterasyon: HANDOFF/state dosyaları ve hedef metni gizli anahtar maskesinden geçer (Bearer token düz metin yazılmıyor)
- İterasyon: planlayıcı 'tamam' olmuş maddeyi yeniden planlayamaz; yerelde doğrulanamayan hedefte `done+reason` ile durur; engel ağırlıklı tur ilerleme sayılmaz
- İterasyon: madde içinde yalnızca ilgili test koşulur, tam kontrol tur sonunda (süre ~%40 azalır)
- Komut-ailesi koruması `for/while` döngüsü, `cd &&`, `export;` öneklerini ve `curl <host>` tekrarını tanır (404 uçlarını kaba kuvvetle deneme engellenir)

## [3.2.0] - 2026-10-19

### Eklendi
- İterasyon modu: `evren iterate "<hedef>"` ve REPL'de `/iterate` (kontrol → plan → madde madde düzelt → doğrula → `.evren/iterations/<slug>/HANDOFF.md`); `--rounds`, `--check`, `--item-steps`, `--resume`, `--status`
- `--read-root` / `EVREN_READ_ROOTS`: salt-okunur referans klasör (ör. web projesini okuyup Flutter'a taşımak)
- Kontrol otomatik algılama: flutter (gerekirse `pub get`), pytest, npm lint/test, dotnet build; flutter analyze/test sorun sayımı

### Düzeltildi
- Çalışma alanı dışındaki klasörde list_directory çökmesi; araç istisnası artık görevi bitirmez
- Git Bash biçimli `/d/...` yolları Windows yoluna çevrilir
- Sorun sayısı artan tur ilerleme sayılmaz; `--resume` ilerlemesizlik sayacını sıfırlar

## [3.1.0] - 2026-10-18

### Değişti
- Windows'ta run_command varsayılan kabuğu Git Bash (EVREN_SHELL=auto|bash|cmd|powershell); WSL bash'i kullanılmaz
- REPL'de !komut: ajanla aynı kabukta çalışır, çıktı sonraki göreve bağlam olur
- evren setup-terminal: Windows Terminal'e 'EVREN (Git Bash)' profili ve isteğe bağlı masaüstü kısayolu
- Zaman aşımında Windows Job Object ile tam süreç ağacı sonlandırma; akış kesilince komut geride kalmaz
- k8s hazır komutları önce API sunucusunu kontrol eder; uzak sudo parola beklemez (sudo -n)
- Plan kipi 8 adım ve create_plan hatırlatması; /context ve durum satırı bütçeye göre; son yanıt uzunluk sınırı
- Komut önerisi harf yer değiştirmesini tercih eder (/modle -> /model); düşünme kapalıyken akış zaman aşımı 90 sn

## [3.0.0] - 2026-10-17

2.2.0 sonrası kararlılık, yetenek ve ajan kütüphanesi çalışmaları; major sürüm.

### Eklendi
- **Araç kaydı, merkezi onay ve `delegate(agent=...)`:** araçlar tek kayıt üzerinden geçer, onay akışı merkezileştirildi.
- **Ajan kütüphanesi:** `/agent`, `/agents`, `@ajan` anması ve `/team` ile çoklu ajan çalışması.
- **Satır aralığı düzenleme, doğrulama kapısı, plan→todo geçişi, model kaydı, Clef entegrasyonu, oturum içi kota.**
- **Paralel okuma, hızlı açılış, atomik dosya yazma, görev bazli geri alma, görsel girdi, anlamsal kod arama.**
- **Ayar ve izin kuralları, hook'lar, oturum geçmişi, `apply_patch`, plan akışı, Esc/kuyruk, MCP, `web_search`.**
- **CI kaldırıldı** (GitHub Actions).

### Değişti
- `_run_steps` parçalandı, REPL komut tablosu ve `SessionContext` yeniden düzenlendi.
- İyi özellikler varsayılan açık; otomatik yedek varsayılan kapalı (`EVREN_BACKUP=true` ile açılır).

### Düzeltildi
- `/resume` oturum listesi, ajan modeli ve adım tavanı, şart onayı, Clef istek biçimi.

## [Unreleased]

## [2.2.0] - 2026-10-17

Kararlılık ve hız planı (Faz 0-9) tamamlandı.

### Eklendi
- **Başsız (headless) kip:** `evren -p "İSTEK"` tek görevi REPL'siz çalıştırıp çıkar (`--max-steps` destekli).
- **Görev izi:** her görev `.evren/logs/trace.jsonl`'e adım adım yazılır; `evren debug last [N]` ve REPL'de `/debug last` adım tablosunu gösterir.
- **Yeni REPL komutları:** `/commit`, `/review`, `/init`, `/context`, `/compact`, `/rewind`, `/doctor`, `/rol` (kalıcı rol, ayarlarda saklanır), `/debug`; `.evren/commands/*.md` özel komutları otomatik kaydedilir.
- **`@dosya` anması:** görev metnindeki `@yol` ifadeleri dosya içeriğiyle değiştirilir.
- **Akıllı yapıştırma:** `/` ile başlayan ama kayıtlı komut olmayan metinler görev sayılır; uzun yapıştırmalar `.evren/tmp/paste-N.txt`'ye kaydedilip kısaltılır; yanlış yazılan komut için "bunu mu demek istediniz" önerisi.
- **Canlı benchmark:** `bench/tasks.json` + `scripts/bench.py` ve GitHub Actions CI (metin/sır taraması dahil).

### Değişti
- Mutlak bağlam bütçesi ve akıllı sıkıştırma: büyük araç çıktıları çıktı türüne göre özetlenir, yeni görevde önceki görevlerin çıktıları baş/son kısmına kırpılır; sabit önek küçültüldü.

## [2.1.2] - 2026-10-17

### Düzeltildi
- Araç döngüsü: aynı araç aynı argümanla, arada dosya değişmeden tekrar çağrılırsa çalıştırılmaz
  (`TEKRAR ENGELLENDİ`); aynı çıktı tekrar gelirse işaretlenir; 3 tekrardan sonra sonraki tur araçsız
  özet ister. Bir REPL oturumunda aynı `git grep` tur başına 25+ kez çalışıp ~2M token yakıyordu.
- Dosya değiştirebilen komutlar (git checkout/pull/commit, install, rm/mv, yönlendirme) ayrı bir
  sezgiyle tespit edilir; okuma komutları (`git grep`, findstr, testler, build) korumayı sıfırlamaz.
- Yeni görevde önceki görevlerin büyük araç çıktıları baş/son kısmına kırpılır.
- `edit_file`: tüm `edits` maddelerinde aynı `path` varsa üst seviyeye taşınır.
- Çok satırlı hedef eşleşmediğinde hata mesajı ilk farklı satırı gösterir.
- REPL açılışında `docs_result` NameError'u giderildi; açılış duman testi eklendi.
- Banner ve açılış sadeleştirildi; tek seferlik komutlarda kullanım şartları satırı basılmaz.

## [2.1.1] - 2026-10-15

### Değişti
- Marka temizliği: kod tabanındaki tüm "Antigravity" atıfları kaldırıldı; proje artık yalnızca
  EVREN CLI adıyla anılıyor (docstring'ler, CLI açıklamaları, TUI başlığı, kurulum betikleri,
  pyproject, README ve AGENTS.md).
- Açılış ekranı sadeleştirildi: banner'daki yapılandırma satırı tek satıra sığıyor (yollar dosya
  adıyla kısaltılıyor, anahtar kaynağı .env ile aynıysa yalnızca ".env" yazılıyor), sürüm notları
  56 karaktere indirildi, düz modda çalışma alanı yolu kısaltılıyor; bağlam özeti banner'dan
  önce tek satır olarak basılıyor, sorun notları ayrı uyarı olarak veriliyor.
- Yanıt akışı artık canlı markdown olarak çiziliyor: ham `**`/`#` işaretleri ekrana düşmüyor,
  düz modda satır satır temiz metin basılıyor.
- Araç çağrısı satırı tek satıra indirildi: varsayılan/boş argümanlar gizleniyor, onay
  uyarıları yalnızca gerçek onay gerektiğinde basılıyor.
- Görev sonunda tek satırlık özet: adım • süre • token.
- Adım tavanı varsayılanı 20 → 30 (`EVREN_MAX_STEPS`); son 3 adımda doğrulamayı hatırlatan
  adım uyarısı eklendi.
- `edit_file` hata mesajı çok satırlı hedefte ilk farklı satırı gösteriyor (kör tekrarların
  önüne geçer).

### Eklendi
- `/help` görev kalıbı bölümü güncel akışı anlatıyor; `tests/test_repl_startup.py` ile
  açılış davranışı test kapsamına alındı.

## [2.1.0] - 2026-10-15

Claude Code'un temel yetenekleri. Özellikler EVREN'in kendisine geliştirtilip (geçici klonda),
incelenip düzeltilerek alındı.

### Eklendi
- **Kullanıcı tanımlı beceriler:** `<cli_home>/skills/*.md` (front-matter: name, title, category,
  description, when_to_use; gövdedeki liste satırları adımlar). Aynı adlı yerleşik beceriyi
  gölgeler; `/skills` `[user]` etiketiyle listeler, `/skills reload` yeniden yükler, `/skill <ad>`
  uygular (`/apply` eşdeğeri). Adımı/açıklaması olmayan dosya uyarıyla atlanır; beceriler ilk
  kullanımda tembel yüklenir.
- **`todo_write` aracı (TodoWrite):** çok adımlı işlerde yapılacaklar listesi (pending / in_progress /
  completed, aynı anda tek in_progress). Her güncellemede tam checklist terminalde gösterilir,
  `/todos` ile görülür, `/clear` sıfırlar. Sistem prompt'u 3+ adımlı işlerde kullanmasını söyler.
- **`web_fetch` aracı (WebFetch):** http/https GET; HTML okunur metne (başlık `<title>`, `#` başlıklar,
  `metin (url)` bağlantılar, tırnaksız öznitelikler dahil), JSON/düz metin aynen; boyut, süre ve
  12.000 karakter sınırı; ikili içerik (PDF, resim) reddedilir. `EVREN_CA_BUNDLE`/`EVREN_SSL_VERIFY`
  ayarlarına uyar; URL'deki gizli değer yer tutucuları yalnızca istekte açılır. Tüm kiplerde salt-okuma.
- **Arka plan komutları:** `run_command(run_in_background=true)` hemen `bg-N` kimliği döndürür;
  `check_background` son okumadan bu yana gelen çıktıyı ve durumu/çıkış kodunu, `stop_background`
  süreç ağacını sonlandırır; `/bg` ve `/bg stop <id>`. REPL ve `evren agent` kapanırken tüm arka plan
  ağaçları temizlenir. Ön plandakiyle aynı onay kapısından geçer; ask/plan kipinde reddedilir.
  `check_background` süreç çalışıyorsa bitmesini varsayılan 20 sn bekler (`wait_seconds`, 0-300;
  bitince hemen döner): beklemesiz sürümde model 18 kez art arda yoklayıp adım bütçesini tüketmişti.
- **`delegate` aracı (Task / alt ajan):** bağımsız bir araştırma alt görevi temiz bağlamlı bir alt
  oturuma devredilir; mevcut `_run_steps` döngüsü ASK kipinde (salt-okuma) yeniden kullanılır, ana
  geçmişe yalnızca `ALT AJAN ÖZETİ` girer. Yalnızca normal kipte; kip kapısı alt ajanın yeniden
  `delegate` çağırmasını engeller (özyineleme yok).
- **Git farkındalığı:** çalışma alanı bir git deposuysa sistem bağlamına oturum başında bir kez dal,
  ana dal tahmini, son 3 commit ve değişen/izlenmeyen dosya sayısı eklenir; git yoksa sessizce
  atlanır, her komut 2 sn ile sınırlı. Özet REPL'deki her görevde yeniden üretilmez (sistem mesajı
  önbelleği korunur); yalnızca `/clear` yeniler.
- Yerel çalıştırma bağlamına bugünün tarihi eklendi (`Bugünün tarihi: YYYY-MM-DD`); yalnızca tarih,
  saat yok — sistem mesajı adımlar arasında birebir aynı kalıp prompt önbelleğini korur. Model
  tarihli bir işlem için artık `date /t` komutu çalıştırmak zorunda değil.

### Değişti
- `find_files(query=...)` artık `grep -n` gibi `yol:satır: metin` döndürür (büyük/küçük harf
  duyarsız, `a|b` alternatifleri); önceden yalnızca "(Eşleşti)" diyordu ve model `findstr`'e düşüyordu.
- Brief'te açık plan isteği ("planla", "plan yap", "önce plan") boyuttan bağımsız planlayıcıya gider.
  Planlayıcı adım tavanı 3 → 4 ve satır numaralı plan yazar; plan varsa uygulayıcının adım bütçesi
  plan maddesi başına ~3 adım (+5, en fazla 40) olarak büyür (`--max-steps`/`/steps` verilmişse dokunulmaz).
- `.py`/`.json` dosyası yazılınca/düzenlenince otomatik sözdizimi kontrolü; hata aynı turda bildirilir.
- `edit_file`: zaten uygulanmış düzenleme hata değil `BİLGİ: ... ZATEN UYGULANMIŞ` döner (çoklu
  düzenlemede diğerleri uygulanır); `placeholder` gibi deneme hedefleri reddedilir ve `view_file`
  önerilir; var olmayan dosyada `write_file` önerilir.
- `use_powershell=true` ile elle yazılmış `powershell -Command "..."` sarmalaması otomatik açılır.
- Sürüm kaynaktan çalışırken `pyproject.toml`'dan okunur (editable kurulumda meta veri bayatlıyordu).
- Sistem prompt'una commit kuralı: commit'ten önce `git log -5 --oneline` ile projenin mesaj biçimine
  bakılır, başlık NE değiştiğini anlatır (sürüm commit'inde ana değişiklikler özetlenir), Türkçe
  karakterler ASCII'ye çevrilmez, kullanıcı istemedikçe push yapılmaz.

### Düzeltildi
- Arka plan komutu gizli değer ortam değişkenleriyle başlatılınca `PATH` dahil tüm ortam siliniyordu;
  üst sürecin ortamıyla birleştirilir. Arka plan yolu kullanıcı onayını da atlıyordu.
- Hata metni boş olan istisnalar (`StopIteration` vb.) `Hata: ` olarak raporlanıyordu; tür adı eklenir.
- REPL Windows konsolu olmadan (Git Bash/mintty, borulu girdi, CI) açılışta
  `NoConsoleScreenBufferError` ile çöküyordu; düz `input()`'a düşülür.
- `test_repl_wrapper_passes_current_model_and_local_context` git'te izlenmeyen `agent.md`'ye
  bağlıydı; temiz klonda düşüyordu.

## [2.0.0] - 2026-10-14

### Değişti
- Sistem prompt'u sadeleştirildi ve çelişkileri giderildi: "her prompt için zorunlu
  netleştirme / asla tahmin etme" kuralı kaldırıldı. Ajan makul varsayımları kendisi yapar,
  araçla bulunabilecek bilgiyi sormaz; `ask_user` yalnızca gerçekten bloklayıcı veya riskli
  durumda kullanılır. `AGENTS.md` aynı kurala hizalandı.
- "Her turda yalnızca tek küçük adım" kuralı kaldırıldı: bağımsız okumalar/aramalar aynı turda
  toplanır. Adım tavanı 12 → **20**.
- Adım başına talimat artık sistem mesajına eklenmiyor. Sistem mesajı ve geçmiş adımlar arasında
  birebir aynı kalır (sunucu prompt önbelleği isabet eder); yalnızca son adımlarda ve kesilme
  sonrasında geçici, geçmişe yazılmayan kısa bir not gönderilir.
- HTTP/Argo CD notları sistem prompt'undan çıkarıldı; yalnızca görev metni bu konuları
  içerdiğinde göreve eklenir.
- `qwen3.8-flash-next` çıktı tabanı 16384 → **8192**. Stall tavanı 8192 → **16384**
  (eski değer qwen tabanı nedeniyle hiç etkili değildi).
- Kesilen/boş yanıttan sonraki deneme artık `thinking: false` ile gider: tıkanma neredeyse her
  zaman gizli düşünmenin bütçeyi tüketmesidir, düşünmeyi kapatmak döngüyü kırar.
- Prompt hazırlama (netleştirme) çağrısı `thinking: false` ile gider; küçük bir JSON şemasını
  doldurmak düşünme gerektirmez.
- `view_file` aralıksız çağrıda ilk 250 satırı, aralıkla en fazla 400 satırı / 24k karakteri
  döndürür ve devam için ipucu verir (önceden 150k karaktere kadar tüm dosya).
- **Hızlı varsayılanlar:** görev öncesi hiçbir API çağrısı yapılmaz. Netleştirme turu varsayılan
  0 (`EVREN_CLARIFY_ROUNDS`, 0-4; ajan gerekirse `ask_user` ile sorar), Rol/Çerçeve/Kısıt/Çıktı
  öneri çağrısı kapalı (`EVREN_FRAME_SUGGEST=true`) ve bu alanlar artık tek tek sorulmaz
  (`EVREN_FORM_FIELDS=true` ile açılır); form yalnızca Brief ister.
- Normal kipteki her görev varsayılan olarak `thinking: false` ile çalışır (plan olsun ya da
  olmasın); planlayıcı, plan kipi ve ask kipi düşünmeye devam eder.
  `EVREN_EXECUTOR_THINKING=true` eski davranışı getirir.
  Hazırlık talimatındaki alana özgü PostgreSQL yamaları çıkarıldı.
- Bağlam belgeleri "uygulamadan önce doğrula" yerine "hazır bağlam, çelişkide canlı dosya esastır"
  ifadesiyle verilir; model bilgiyi yeniden doğrulamak için adım harcamaz.

### Eklendi
- **Planlayıcı / uygulayıcı model ayrımı.** Normal kipte brief `EVREN_ROUTE_MIN_TOKENS`
  (800) tahmini token'ı aşarsa önce `EVREN_PLANNER_MODEL` (varsayılan `deepseek-v4.1-flash`)
  salt-okuma araçlarıyla en fazla `EVREN_PLANNER_STEPS` (3) adımda numaralı bir plan çıkarır;
  ardından `EVREN_EXECUTOR_MODEL` (varsayılan oturum modeli) planı düşünmeyi kapatarak
  (`thinking: false`, `EVREN_EXECUTOR_THINKING=true` ile açılır) uygular. Plan kipi doğrudan
  planlayıcı modelde çalışır. `/continue` ve takip yanıtları yeniden planlamaz, aynı uygulayıcıyla
  sürer. Plan çıkarılamazsa görev normal biçimde oturum modeliyle sürer. `EVREN_AUTO_ROUTE=false`
  özelliği kapatır. `/model` ile seçilen oturum modeli görev sonunda geri yüklenir.
- `thinking: false` desteği (`EvrenClient.chat_* (thinking=False)`). Canlı ölçüm: qwen3.8-flash-next,
  deepseek-v4.1-flash ve mimo-v2.6-pro temiz yanıt verir; glm-5.3 düşünme metnini `content`
  içine sızdırdığı için glm'e gönderilmez. Sunucu alanı reddederse istemci onsuz yeniden dener.
- Kullanım loguna `cached_tokens` (sunucu prompt önbelleği isabeti) eklendi; `/tokens`
  önbellek isabet oranını gösterir.
- **Gizli değer kasası** (`src/secret_vault.py`): görev metnindeki Bearer/Basic token, parola,
  API anahtarı ve URL içi parolalar modele gitmeden `{{EVREN_SECRET_n}}` yer tutucusuna alınır.
  `run_command`/`ssh_run` gerçek değeri yalnızca çalıştırırken koyar ve `EVREN_SECRET_n` ortam
  değişkeni olarak verir; terminal, araç çıktısı, SSH denetim logu, `agent.md`/`changelog.md`
  yalnızca yer tutucuyu görür. Önceden token LLM'e gönderiliyor, betiklere ve bağlam belgelerine
  düz metin yazılıyordu.
- `run_command` için `use_python`: kod `sys.executable -c` ile argv olarak çalışır (çok satırlı,
  kaçışsız). Windows'ta çok satırlı `python -c "..."` otomatik bu yola çevrilir; diğer çok satırlı
  cmd komutları sessizce bozulmak yerine açık hata döndürür.
- Drone CI notları (`drone` geçen görevlere eklenir): son build'ler tek istekte
  `GET /api/user/repos?latest=true`; log ucu adım numarası ister.
- Sistem prompt'u: istenen çıktıya ulaşınca dur (kendiliğinden log/kök neden analizi yok);
  sorgu işlerinde çalışma alanına ara dosya yazma.
- **SSH host kayıtları birleştirildi:** `~/.evren/ssh_hosts.json` (makine geneli) ve
  `<proje>/.evren/ssh_hosts.json` birlikte okunur, çakışmada proje kazanır. Önceden proje dosyası
  varsa makine genelindeki tüm host'lar görünmüyordu (ör. `koyu3-k8s` yalnızca başka bir dizinde
  çalışıyordu). `/ssh add` ve `evren ssh add` artık varsayılan olarak makine geneline yazar.
- Bilinmeyen SSH alias'ında hata kayıtlı alias'ları ve benzerlerini (hedef + açıklama) listeler;
  benzer ad farklı makine olabileceği için otomatik eşleme yapılmaz.
- SSH bağlantı hatasında (exit 255: timeout, refused, auth) hedef `user@host:port` ile birlikte
  "uzak komut çalışmadı, tekrarlama" bildirilir.
- Uzun komut çıktısı (`ssh_run` ve `run_command`) kullanıcının terminaline tam basılır,
  `.evren/tmp/` altına kaydedilir; modele kırpılmış görünüm + "tabloyu yeniden yazma" notu gider.
  Düz "listele/göster" görevlerinde bundan sonraki tur `tool_choice="none"` ile yalnızca kısa özet ister.
- Görev metninde kayıtlı bir SSH alias'ı geçerse göreve hedef notu eklenir: işler o host'ta
  `ssh_run` ile yapılır (önceden model kubectl'i yerelde çalıştırıp başka bir kümeyi listeleyebiliyordu).
- Kubernetes notları (`kubectl/k8s/pod/namespace` geçen görevlere): podların request/limit
  değerleri için container başına satır veren tek `go-template | column -t` sorgusu.
- Öneri/teşhis görevleri için "önce tek geniş anlık görüntü, sonra kanıta dayalı öneri" kuralı.
- **Hazır uzak komutlar** (`src/remote_presets.py`): `ssh_run(alias, preset="k8s_health")` (durum sayıları,
  hazır olmayan podlar, Running'de yüksek restart, uyarı event'leri, düğüm/bellek, boot ve k3s başlangıcı,
  limitsiz container sayısı) ve `preset="k8s_resources"` (container başına request/limit tablosu);
  `namespace` ile tek namespace. Komutu model kopyalamaz, EVREN üretir: kopyalar bozuluyordu (düşünme
  turunda yanlış "0 limitsiz") ve prompt'taki sürümde go-template `
` kaçışı gerçek satır sonuna
  dönmüştü. Preset'ler salt-okumadır, ask/plan kipinde izinlidir. Canlı ölçüm: "pod durumları için öneri"
  4-9 istek / 34-96K token → 2 istek / ~14K token, 13 sn.
- Araç çağrısı satırında kısa argümanlar önce yazılır (uzun `command`, `alias=`'ı ekrandan itiyordu).
- Sistem prompt'u: deneme/yer tutucu komut (`echo test` vb.) çalıştırma.
- Modele giden komut çıktısı penceresi 4000 → 8000 karakter (teşhis çıktısının ortası kesiliyordu).
- Akışlı isteklerde `stream_options.include_usage` gönderilir; önceden bazı turların token kullanımı
  `/tokens` ve `usage.jsonl`'a hiç yazılmıyordu.
- Komut çıktısı terminalde artık konsol genişliğinde zorla bölünmez (`soft_wrap`), geniş tablolar okunur.
- `run_command` için `use_powershell` parametresi: komut PowerShell betiği olarak argv ile
  çalışır; cmd.exe altında `powershell -Command "..."` iç içe tırnak hataları ortadan kalkar.

### Düzeltildi
- `ask_user` ask/plan modlarında modele sunuluyor fakat gateway tarafından reddediliyordu;
  model "REDDEDİLDİ" döngüsüne giriyordu. Artık tüm modlarda izinlidir.
- `project_docs.py` içinde eksik `re` içe aktarması (changelog girdisi yazılamıyordu).
- Çoklu anahtarda havuz hep ilk anahtara yapışıyor, ancak 429 sonrası geçiyordu; neredeyse
  boş bir anahtar turu yarıda kesebiliyordu. REPL ve tek seferlik `chat`/`agent` artık
  başlangıçta tüm anahtarların günlük kotasını paralel okur, en doluyu sabitler ve
  `EVREN_KEY_MIN_REMAINING` (50k) altındakileri oturum için park eder. Banner aktif anahtarı gösterir.
- Kota normalizasyonu günlük kalanı 5 dakikalık pencere tavanıyla eşliyordu
  (`1,086 / 2,500,000`); artık `daily_limit_tokens` / `daily_used_tokens` önceliklidir.
- `test_updater` testleri gerçek `.evren/logs/update.log` dosyasına yazıyordu; geçici dizine yönlendirildi.
- `redact_text`: `Authorization: Bearer <tok>` biçiminde yalnızca "Bearer" kelimesi maskeleniyor,
  token açıkta kalıyordu (Basic için de). Önce kimlik doğrulama şeması maskelenir.

## [1.1.0] - 2026-10-14

### Eklendi
- Kalıcı API kullanım logu: her yanıtın token sayaçları `.evren/logs/usage.jsonl`
  dosyasına tek satır JSON olarak eklenir (`src/usage_log.py`). Yalnızca sayaç
  alanları yazılır; mesaj içeriği, araç argümanları ve API anahtarları loglanmaz,
  model adı paylaşım maskelemesinden geçer. Dosya 5000 satırda bir `usage.jsonl.1`
  olarak arşivlenir. Loglama hatası bir ajan turunu asla bozmaz.
- `/tokens` raporu artık oturum toplamını (istek sayısı, prompt/completion/total)
  ve kalıcı logdan gelen son isteklerin ortalama prompt boyutunu gösterir.
- `/steps [n]` komutu adım tavanını gösterir veya oturum için ayarlar; `--max-steps`
  artık opsiyoneldir ve verilmediğinde `EVREN_MAX_STEPS` kullanılır.
- Yeni yapılandırma değişkenleri: `EVREN_MAX_STEPS` (12), `EVREN_COMPACT_RATIO` (0.6),
  `EVREN_STALL_TOKEN_CAP` (8192); `EVREN_DOCS_TOKENS` varsayılanı 3500'den 1200'e indi.

### Değişti
- Ajan adım tavanı 30'dan **12**'ye indirildi. Her adım tam sistem prompt'unu ve
  araç şemalarını yeniden gönderdiği için taban maliyet adım sayısıyla doğrusal
  artar; iş bitmezse `/continue` ile uzatılır. Son iki adımda modele adım sınırının
  sonunda olduğu bildirilir.
- Bağlam sıkıştırma artık pencerenin tamamı dolmadan, **%60**'ında tetiklenir.
  Eski davranışta 128k pencere dolana kadar geçmiş her adımda tamamıyla
  yeniden gönderiliyor ve maliyet kuadratik büyüyordu. İlk sıkıştırma bir kez
  bildirilir; `EVREN_COMPACT_RATIO=1.0` eski davranışı geri getirir.
- Bağlam belgesi bütçesi 3500 → **1200** token; bölüm sırası sinyal başına maliyete
  göre `conventions → current_focus → identity → runtime_env → project_map` oldu,
  proje haritası satır tavanı 60 → 25'e indirildi. `agent.md` dosyasının kendi
  bölüm sırası değişmedi.
- `ask` ve `plan` modlarında yazma araçlarının şemaları modele artık gönderilmiyor
  (gateway bunları zaten reddediyordu); `tool_gateway` otorite olmaya devam eder.
- Kesilen/boş yanıt durumunda katlanan çıktı bütçesinin tavanı 32768 → **8192**;
  ikinci tekrarda sıkıştırma eşiği yarıya indirilir. Kalıcı hata mesajı `/tokens`
  ve `/steps` ile ayar düğmelerini gösterir.
- Prompt hazırlama (netleştirme) çağrısı tam yürütme bağlamı yerine kısa proje
  özetini alır; bu çağrı ajan çalışmadan önce yapılan ayrı bir istek olduğu için
  tüm yükü fazladan maliyetti.

### Düzeltildi
- `test_apply_frame_merges_identity_and_keeps_base` ve prompt-hazırlama sarmalayıcı
  testi, `workspace_root=None` üzerinden gerçek depo içeriğine bağımlıydı; izole
  geçici çalışma alanı kullanırlar.

Ayrıntılı plan ve ölçüm: `plans/token-consumption-fixes/plan.md`.

## [1.0.1] - 2026-10-14

### Eklendi
- Karşılama paneli artık çalışma alanının yanında API anahtarlarının hangi kaynaktan
  okunduğunu ve okunan dosyaların tam yolunu gösterir: süreç ortam değişkenleri →
  `.env` (`.env` → depo kökü → `~/.evren/.env`) → `~/.evren-cli/config.json` `ApiKeys`.
  Panel yalnızca kaynak adı, dosya yolu ve anahtar adedi basar; anahtar değerleri
  maskelenmiş olarak bile yazdırılmaz.
- `config.config_source_report()` yapılandırma kaynağını programatik olarak döndürür;
  `print_banner()` yeni `config_info` parametresini alır ve hata durumunda da açılır.

## [1.0.0] - 2026-10-14

### Eklendi
- Akıllı bağlam dosyaları: CLI her çalıştığında çalışma dizininde `agent.md` ve oturum
  günlüğü (`changelog.md`) oluşturur, CLI ana dizininde `skills.md`, `lessons.md` ve
  `commands.md` dosyalarını yönetir; bu dosyalar prompt gönderilmeden önce okunup ajan
  sistem istemine ve prompt hazırlama (netleştirme/öneri) çağrısına eklenir.
- Yönetilen blok birleştirme (`src/docs_merge.py`): yalnızca `evren:managed` işaretli
  bölümler ve yalnızca içerik gerçekten değiştiğinde yazılır; elle yazılan metin,
  CRLF ve BOM korunur. Yönetilen blok içermeyen dosyaya dokunulmaz.
- NTFS büyük/küçük harf koruması: mevcut `AGENTS.md` veya `CHANGELOG.md` varsa CLI o
  dosyaları asla ezmez, `evren-agent.md` / `evren-changelog.md` kullanır.
- Yeni komutlar: `evren docs [status|refresh [--adopt]|unmark|path]` ve REPL'de `/docs`.
- Yeni çevre değişkenleri: `EVREN_AUTO_DOCS` (varsayılan `true`), `EVREN_DOCS_TOKENS`
  (varsayılan `3500`), `EVREN_CLI_HOME` (salt-okunur kurulumlar için belge dizini).
- Her bağlam dosyası yazımı `.evren/backups/` altına yedeklenir ve sır maskelemesinden
  (`redact_text`) geçer; `ask` ve `plan` kiplerinde hiçbir bağlam dosyası yazılmaz.

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
