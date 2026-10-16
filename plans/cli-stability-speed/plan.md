# EVREN CLI — Kararlılık ve Hız Planı

**Amaç:** EVREN; EVREN LLM API'nin günlük kotası içinde çalışan, Türkçe, Windows ağırlıklı, Claude Code
benzeri bir ajan (kod geliştirme + SSH/Kubernetes/Drone operasyonları). Bu plan onu **daha az token ile,
daha hızlı ve regresyonsuz** çalışır hale getirmek içindir. Her faz ölçümle başlar, ölçümle kapanır.

Tarih: 2026-10-17 · Taban: `8a57806` (2.1.2) · 763 test

---

## 1. Ölçülen durum (kanıt)

### Gerçek kullanım (`.evren/logs/usage.jsonl`, 3 çalışma alanı, 14–16 Ekim, 4.023 istek)

| Ölçüm | Değer | Anlamı |
|---|---|---|
| İstek başına prompt (medyan / p90 / maks) | **64K / 79K / 83K** token | Oturumlar sıkıştırma tavanının (128K × %60 = 77K) hemen altında sürekli dolu çalışıyor |
| Maliyetin girdi payı | **%99,4** (çıktı 1,46M, prompt 225,7M) | Hız/maliyet kaldıracı neredeyse tamamen bağlam boyutu |
| Önbellek isabeti | %63–70 | İyi; ama kalan %30–37 yine on milyonlarca token |
| 20. adım ve sonrası istekler | **%24,5** | Uzun görevler yaygın; döngüler ve kota tükenmesi buradan |
| Tek gün (15 Ekim) | 2.535 istek, 147M prompt | 6 anahtarın günlük kotası bu yüzden bitti |
| Model | qwen3.8-flash-next %99 | Uygulayıcı kalitesi neredeyse tek modele bağlı |

### Sabit yük (her istekte)

- Sistem prompt'u **6.125 karakter** (ilk sadeleştirmede 3.329'a inmişti; yamalarla yeniden büyüdü).
- 17 araç şeması **11.971 karakter**; en büyükleri `run_command` 1.537, `edit_file` 1.223, `ssh_run` 1.097.
- Toplam ≈ 5–6K token sabit önek; SSH araçları kayıtlı host olmasa da gönderiliyor.

### Kod yapısı

| Modül | Satır | En uzun fonksiyon |
|---|---|---|
| `agent.py` | 1.528 | `_run_steps` **361** satır |
| `repl.py` | 1.334 | `_repl_loop` **559** satır |
| `tools.py` | 1.292 | `execute_tool_call` 175 satır |

- Onay kodu araçlara dağılmış: `tools.py`'de 8 `prompt_confirm`, 3 kopya "Ajan … istiyor" uyarısı.
- Araç nitelikleri 6+ ayrı küme/`if` zincirinde: `READ_ONLY_TOOLS`, `ASK_HIDDEN_TOOLS`, `PLAN_HIDDEN_TOOLS`,
  `_REPEAT_EXEMPT_TOOLS`, `WRITE_TOOLS`, `command_may_modify_files`, dispatcher dalları.
- Küresel durum: `secret_vault`, `todos`, `background`, `skills` (USER_SKILLS), `ui` (_STREAM) modül düzeyinde.
- **CI yok.**

### Bu çalışmada testler geçerken kaçan regresyonlar

REPL açılışta `NameError` · `ensure_cli_docs()` sessizce silindi · arka plan komutu onayı atlıyordu ·
gizli değer varken `PATH` siliniyordu · `delegate` kip kapısının önünde (özyineleme) · `ask_user` sunulup
reddediliyordu · `git grep` döngü korumasını sıfırlıyordu · bozuk JSON/regex kaçışları.

**Ortak kök:** (a) bir aracın niteliği tek yerde tanımlı değil, her yeni özellik bir kümeyi unutuyor;
(b) uçtan uca test yok, birim testler parçaları ayrı ayrı doğruluyor.

### Açılış

Isınmış: `evren --version` 0,43 sn, REPL açılıp kapanma 2,7 sn (çoğu 6 anahtarın kota sorgusu + şart
kontrolü). İçe aktarmanın ~1 sn'si OpenAI SDK. Öncelik değil, ama ucuz kazanç.

### Komut ve girdi kullanımı (REPL geçmişi, 4 dosya, 694 girdi)

| Ölçüm | Değer | Anlamı |
|---|---|---|
| Çok satırlı yapıştırma | **92 girdi**, ort. 65 satır, en büyüğü 1.327 satır / 44.538 kr | Build hatası ve tarama çıktısı yapıştırmak ana kullanım; tamamı geçmişe girip her adımda yeniden gönderiliyor |
| `/` ile başlayan yapıştırma | **10** (`/src/...csproj : error ...`) | `prompt_frame.py:313` bunları komut sanıp "Bilinmeyen komut" ile reddediyor (**hata**) |
| Kullanılan komutlar | `/ssh` 31 · `/keys` 21 · `/clear` 20 · `/continue` 19 · `/exit` 15 · `/quota` 10 · `/model` 7 · `/update` 4 · `/help` 1 | 9 komut kullanılıyor |
| Kullanılmayan komutlar | **40+** (`/blockers /decisions /diagnose /playbook /progress /template /track /task /checkpoint /resume /run /scan /files /drop /add /effort /language /memory /mode /steps /tokens /undo /docs /skills` …) | Çoğu eski Foreman mirası; keşfedilebilirlik ve bakım yükü |
| Var olmayan ama denenen | `/next` 2 · `/commit` 1 · `/cleare` (yazım hatası) 1 | Beklenen komutlar ve öneri eksikliği |
| En sık kısa yanıtlar | "devam" 34 · "kalan işleri tamamla" 6 · "onaylıyorum" 4 | Devam akışı birinci sınıf olmalı; her "onaylıyorum" baştan keşfe dönüyordu |
| Rol girişi | "uzman devops mühendisi" vb. **30+ kez** elle yazılmış | Kalıcı rol/persona ayarı yok |

### Claude Code özellik karşılaştırması (özet; ayrıntı Faz 8)

Var: dosya okuma/yazma/düzenleme, içerik arama, kabuk + arka plan, `todo_write`, `web_fetch`, alt ajan
(`delegate`), plan/ask kipleri, beceriler, bağlam belgeleri (agent.md/AGENTS.md), sıkıştırma, geri alma,
git farkındalığı, gizli değer kasası. **Yok:** ayar dosyası + izin kuralları, hook'lar, `@dosya` ekleme,
özel komut dosyaları, oturum geçmişi/devam listesi, headless JSON çıktı, MCP, görsel girdi.

---

## 2. İlkeler

1. **Ölç → değiştir → ölç.** Her faz bir benchmark kapısıyla kapanır; iyileşme kanıtsız kabul edilmez.
2. **Davranış önce kilitlenir.** Refactor'dan önce senaryo testleri; davranış değişikliği ayrı commit.
3. **Kural yerine yapı.** Prompt'a kural eklemek son çare; tercih sırası: araç tasarımı → kod koruması → prompt.
4. **Küçük, geri alınabilir adımlar.** Her madde tek PR/commit; testler + bench yeşil.

---

## 3. Fazlar

### Faz 0 — Ölçüm ve güvenlik ağı *(önce bu; diğer her şeyin ön koşulu)*

| # | İş | Neden | Kabul |
|---|---|---|---|
| 0.1 | **Senaryo (replay) testleri:** sahte istemciye senaryo dosyasından (YAML/JSON) model yanıtları; `run_step` ve REPL uçtan uca. Bu çalışmadaki 8 regresyon birer senaryo olur. | Birim testler regresyonları kaçırdı | 8 senaryo; her biri eski hatalı kodda kırmızı |
| 0.2 | **Canlı benchmark** `scripts/bench.py`: 6 sabit görev geçici klonda (Drone hatalı repolar, k8s listeleme, k8s öneri, sürüm+test, skills özelliği, marka temizliği); istek, prompt/önbellek token, süre, başarı, tekrar engeli sayısı → `bench/results/<tarih>.json` | Bu oturumdaki ölçümler elle yapıldı | Taban çizgisi kayıtlı; elle tetiklenir (kota) |
| 0.3 | **Görev izi** `.evren/logs/trace.jsonl` (adım, araç, maskeli argüman özeti, süre, sonuç boyu, prompt token) + `evren debug last` | Döngüler log elle ayrıştırılarak bulundu | Son görevin adım tablosu tek komutla |
| 0.4 | **CI** (GitHub Actions, Windows + Ubuntu): pytest, `compileall`, REPL açılış duman testi, kontrol karakteri ve sır taraması | Hiç CI yok | `master`'a her push'ta yeşil |

### Faz 1 — Bağlam boyutu *(en büyük hız ve kota kazancı)*

| # | İş | Neden | Kabul |
|---|---|---|---|
| 1.1 | **Mutlak bağlam bütçesi** `EVREN_CONTEXT_BUDGET` (öneri 32K); pencere oranı yerine | Medyan 64K, oran tabanlı tavan (77K) hep dolu | Bench'te istek medyanı ≤ 25K |
| 1.2 | **Akıllı sıkıştırma:** eski araç çıktısı → tek satır künye (`view_file a.py 1-250 okundu`), ortadaki turlar → ucuz özet çağrısı (`thinking:false`); görev sınırında önceki görev tek paragraf özete iner | Şu an ortadan körlemesine atıyor, her seferinde 3 mesaj | Görev başına toplam prompt %50↓, başarı aynı |
| 1.3 | **Okuma tekrarı:** aynı dosya aralığı değişmeden tekrar okunursa "adım N'de okundu, değişmedi" (içerik hash'i) | Uzun görevlerde aynı dosyalar defalarca okunuyor | Trace'te tekrar okuma oranı ölçülür ve düşer |
| 1.4 | **Sabit önek diyeti:** sistem prompt'u ≤ 3,5K kr (kuralları birleştir); araç açıklamaları kısalır; kayıtlı SSH host yoksa SSH araçları hiç gönderilmez; arka plan/delegate/web araçları ihtiyaç anında | 5–6K token × her istek | Önek ≥ %40 küçük; bench başarısı aynı |
| 1.5 | **Çıktı türüne göre özet:** test çıktısı → yalnız hata özeti + son satırlar; uzun grep → ilk 80 eşleşme + sayı | 8K'lık genel pencere test çıktılarında israf | Test adımlarının sonuç boyu %60↓ |

### Faz 2 — Tek araç kaydı ve modüler çekirdek *(kararlılık)*

| # | İş | Neden | Kabul |
|---|---|---|---|
| 2.1 | **`src/tool_registry.py`:** her araç tek yerde: şema + handler + nitelikler (`read_only`, `mutates`, `needs_approval`, `modes`, `repeat_exempt`). Kip filtresi, kapı, tekrar koruması, onay hepsi buradan türer | 6+ dağınık küme; regresyonların yarısı buradan | Kümeler silinir; yeni araç = tek kayıt |
| 2.2 | **Merkezi onay/denetim katmanı:** onay, kasa maskeleme, audit, sonuç kırpma dispatcher'da bir kez | 8 kopya onay; arka plan yolu onayı atladı | Onay atlama senaryosu kalıcı yeşil |
| 2.3 | `_run_steps` (361) → `StepRunner`: istek kur / akış / yanıt sınıflandır / araç yürüt | Değiştirmesi riskli, test edilemez | Fonksiyonlar ≤ 80 satır, davranış aynı |
| 2.4 | `_repl_loop` (559) → komut tablosu (`komut → handler`); `/help` tablodan üretilir | Değişken adı değişikliği REPL'i çökertti | Her komutun kendi testi |
| 2.5 | Küresel durum → `SessionContext` (kasa, todo, arka plan, beceriler, akış) | Testler arası sızıntı; çok oturum imkânsız | Modül düzeyi mutable durum sıfır |

### Faz 3 — Ajan davranışı *(doğru işi ilk seferde)*

| # | İş | Neden | Kabul |
|---|---|---|---|
| 3.1 | **Uygulayıcı model kararı veriyle:** bench'i qwen ve deepseek-v4.1-flash uygulayıcıyla koş, başarı/token/süreye göre varsayılanı seç | İsteklerin %99'u qwen; döngü ve kopyalama hataları hep qwen'de | Karar bench sonucuyla belgelenir |
| 3.2 | **Satır aralığıyla düzenleme** (`edit_file` için `start_line/end_line` modu) + düzenleme başarısızlık oranı trace'ten | Metin eşleşmesi hataları tekrar tekrar adım yedi | Başarısız edit oranı yarıya |
| 3.3 | **Doğrulama kapısı:** kod değiştiren görev "test çalıştırılmadı" diye biterse son tur doğrulamaya zorlanır (proje türünden test komutu) | Görevler test çalıştırmadan bitti | Bench'te doğrulanmamış bitiş = 0 |
| 3.4 | **Plan → todo:** planlayıcı çıktısı otomatik `todo_write` listesine; uygulayıcı oradan ilerler | Plan metni bağlamda kayboluyor | Uzun görevlerde kalan iş her an görünür |
| 3.5 | **Prompt yamaları → preset/beceri:** Drone/Argo/K8s notları `domain_hints`'ten çıkıp preset ve skill dosyalarına | Prompt büyüdü, kopyalama hataları | Sistem prompt'unda alan bilgisi kalmaz |

### Faz 4 — Gecikme

| # | İş | Kabul |
|---|---|---|
| 4.1 | Aynı turdaki bağımsız salt-okuma araçları paralel (thread havuzu) | 3 okuma ≈ 1 okuma süresi |
| 4.2 | OpenAI SDK gecikmeli yükleme; kota + şart kontrolü arka planda, istem hemen gelsin | REPL istemi ≤ 1 sn |
| 4.3 | Uzun akışta Live render kısma (throttle) | Uzun yanıtlarda CPU sabit |

### Faz 5 — Sağlamlık ve kurulum

| # | İş | Neden |
|---|---|---|
| 5.1 | **`evren doctor`:** PATH'teki kopyalar, sürüm/meta veri uyumu, ağ/VPN erişimi, anahtar kotaları, SSH host erişimi | Bu çalışmada 3 ayrı `evren.exe`, bayat meta veri ve VPN kopukluğu elle teşhis edildi |
| 5.2 | Görev bazlı geri alma (`/undo` son görevin tüm dosyaları) + atomik yazma | Yarım kalan görevler bozuk dosya bıraktı |
| 5.3 | Akış ortasında kopmada kaldığı yerden devam; tüm hatalar türüyle | Boş `Hata: ` mesajları |

### Faz 6 — Komutlar ve girdi

| # | İş | Neden (kanıt) | Kabul |
|---|---|---|---|
| 6.1 | **Yapıştırma hatası:** girdi ancak ilk sözcüğü kayıtlı bir komutsa komut sayılır; aksi halde görev metnidir. Bilinmeyen komutta en yakın komut önerilir (`/cleare` → `/clear`) | 10 build hatası yapıştırması reddedildi | `/src/x.csproj : error` görev olarak gider; senaryo testi |
| 6.2 | **Büyük yapıştırma yönetimi:** N satır / K karakter üstü yapıştırma `.evren/tmp/paste-N.txt`'ye kaydedilir; modele `[Yapıştırılan metin #1: 1.327 satır]` + baş/son + hata satırları (`error`, `FAIL`, `CVE-`, `Exception`) gider, gerisi `view_file` ile | 92 yapıştırma, en büyüğü ~12K token; geçmişte kalıp her adımda tekrar gönderiliyor | 44K kr'lik yapıştırma ≤ 3K token; hata satırları kaybolmaz |
| 6.3 | **Komut kaydı:** her komut tek tabloda (ad, takma adlar, açıklama, grup, handler); `/help` ve `/help <komut>` tablodan; prompt_toolkit **otomatik tamamlama** (açıklamalı) | 559 satırlık `elif` zinciri; komutlar keşfedilemiyor | Tab ile tamamlama; her komutun testi (Faz 2.4 ile birlikte) |
| 6.4 | **Sadeleştirme:** kullanılmayan Foreman mirası komutlar (`/blockers /decisions /diagnose /playbook /progress /template /track /task` …) `/legacy` altına ya da kaldırılır; çekirdek ~15 komut kalır | 40+ komut hiç kullanılmamış | `/help` tek ekrana sığar |
| 6.5 | **Beklenen yeni komutlar:** `/commit` (diff özeti → proje biçiminde mesaj → onay → commit), `/review` (git diff incelemesi), `/init` (proje için AGENTS.md üretimi), `/context` (token kırılımı: sistem, araçlar, geçmiş, son çıktılar), `/compact [odak]`, `/rewind` (son görevi geri al, 5.2), `/doctor` (5.1) | `/commit` ve `/next` denenmiş; bağlamın neyle dolduğu görülemiyor | Her biri senaryo testli |
| 6.6 | **Devam akışı birinci sınıf:** "devam / onaylıyorum / kalan işleri tamamla" son görevin todo listesi ve planıyla sürer, keşfi tekrarlamaz; uygulanmamış madde yoksa bunu söyler | "devam" en sık girdi (34); yapıştırılan oturumda her onay baştan keşfe döndü | Devam turunda ilk araç keşif değil, kalan maddedir |
| 6.7 | **Kalıcı rol:** `/rol <metin>` → proje ayarına yazılır (`.evren/settings.json`, 8.1) ve her görevde uygulanır | Rol 30+ kez elle yazıldı | Rol bir kez ayarlanır |

### Faz 7 — Model kullanımı optimizasyonu

Katalogda (EVREN LLM API) kullanılabilenler: `qwen3.8-flash-next` (256K, hızlı), `deepseek-v4.1-flash`
(1M, DeepSWE 74,2), `mimo-v2.6-pro` (1M, en büyük ajan modeli), `glm-5.3` (512K; `thinking:false`'ta düşünme
sızdırıyor), `gemma-4-31b` / `qwen3-vl-30b` (görsel), `qwen3-embedding-8b` + `qwen3-reranker-8b`,
`clef` / `laya` (tipli karar: choice / score / evet-hayır, `/v1/systemone`). Bugün isteklerin %99'u qwen.

| # | İş | Neden | Kabul |
|---|---|---|---|
| 7.1 | **Model rolleri tek yerde** (`src/models.py`): planlayıcı, uygulayıcı, özetleyici, sınıflandırıcı, görsel, gömme; her biri için pencere, `thinking:false` güvenli mi, görsel desteği | Model bilgisi `token_manager`, `config`, `api_client`'a dağılmış | Yeni model = tek kayıt |
| 7.2 | **Uygulayıcı seçimi bench ile** (3.1): qwen / deepseek-v4.1-flash / mimo-v2.6-pro; başarı, token, süre, döngü sayısı | Döngüler ve kopyalama hataları hep qwen'de görüldü; kağıt üstünde deepseek ajan görevlerinde güçlü | Varsayılan veriyle seçilir, CHANGELOG'da gerekçesiyle |
| 7.3 | **Niyet sınıflandırma için Clef:** listeleme / plan isteği / analiz / devam-onay / uzak hedef gibi kararlar tek `systemone` çağrısıyla, olasılıkla; erişilemezse bugünkü regex'lere düşer | `_LISTING_RE`, `_PLAN_INTENT_RE`, `is_execution_followup` kırılgan regex'ler | 694 gerçek girdiden etiketli örnekte doğruluk regex'ten yüksek |
| 7.4 | **Ucuz özetleyici:** sıkıştırma ve büyük yapıştırma özetleri `thinking:false` ile hızlı modelde, uygulayıcının önbelleğini bozmadan | Faz 1.2 ve 6.2 özet ister | Özet çağrısı ≤ 2 sn, görev başarısı aynı |
| 7.5 | **Doğru pencereler** (qwen 256K, deepseek/mimo 1M, glm 512K); asıl sınır mutlak bütçe (1.1) | Bilinmeyen modelde 128K varsayılıyor | Pencere tablosu testli |
| 7.6 | **Önbelleğe duyarlı geçiş:** görev ortasında model değiştirme yok; planlayıcıya kısa bağlam; alt ajan ebeveynle aynı model | Her model geçişi önbelleği sıfırlar | Bench'te önbellek oranı ≥ %70 |
| 7.7 | **Görsel girdi:** `view_file` resimde (png/jpg) görsel modelle betimler; REPL'de ekran görüntüsü yolu yapıştırılabilir | Claude Code resim okur; hata ekran görüntüsü sık | Ekran görüntüsündeki hata metni çıkarılır |
| 7.8 | **Anlamsal kod arama** (`search_code`): repo parçaları `qwen3-embedding-8b` ile `.evren/index`'e (mtime ile artımlı), sonuçlar reranker ile sıralanır | "X nerede, nasıl kullanılıyor" görevleri grep döngüsüne giriyor | Bench'te bu tür görevlerde adım ve token düşer; yoksa özellik kapalı kalır |
| 7.9 | **Kota farkındalığı oturum içinde:** `x-evren-daily-remaining-tokens` başlığı eşiğin altına inince anahtar değişimi; `/tokens` model ve görev bazında maliyet | Anahtar seçimi yalnız açılışta | Oturum ortasında kota bitince kesinti yok |

### Faz 8 — Claude Code özellikleri

| Özellik | EVREN'de durum | Yapılacak | Efor |
|---|---|---|---|
| **Ayar dosyası + izin kuralları** | Yalnız env/.env/config.json ve tek `AUTO_APPROVE` | `~/.evren/settings.json` + `.evren/settings.json` (+ `.local`); `permissions.allow/deny` kalıpları (`run_command(git status*)`, `ssh_run(koyu3-k8s, kubectl get*)`); onay sorusunda "bundan sonra hep izin ver" | M |
| **Hook'lar** | Yok | `pre_tool` / `post_tool` / `stop` kabuk hook'ları ayar dosyasından (ör. düzenlemeden sonra formatlayıcı, tehlikeli kalıbı engelle, bitince bildirim) | M |
| **`@dosya` ekleme** | `/add` var, kullanılmıyor | Görev metninde `@src/a.py` veya `@src/a.py:10-40` → içerik eklenir; tamamlama ile | S |
| **Özel komut dosyaları** | Kullanıcı becerileri var | `.evren/commands/*.md` (`$ARGUMENTS`); beceri ve komut tek kavramda birleşir; proje düzeyi beceri dizini | S |
| **Oturum geçmişi / devam** | Yalnız duraklayan görev checkpoint'i | Oturum dökümleri `.evren/sessions/`; `evren --continue` ve `/resume` listesi (maskeli) | M |
| **Headless / CI kipi** | `evren agent "..."` | `evren -p "..." --output json` (sonuç, değişen dosyalar, token, çıkış kodu), `--allowed-tools`, `--max-steps` | S |
| **Plan kipi akışı** | Plan dosyası yazılıyor | Plan → kullanıcı onayı → aynı oturumda uygulama (ExitPlanMode benzeri); plan todo'ya dökülür (3.4) | S |
| **Arama sağlamlığı** | Sabit dizin atlama listesi | `find_files` `.gitignore`'a uyar, ikili dosyaları atlar | S |
| **Çoklu dosya yama** | `edit_file` tek dosya | `apply_patch` (unified diff, çok dosya, önizleme ve onayla) | M |
| **Durum satırı** | Yok | Model, kip, bağlam doluluğu %, kalan kota (prompt_toolkit alt çubuğu) | S |
| **Kesme ve sıraya alma** | Ctrl+C var | Akış sırasında Esc ile durdurma; çalışırken sonraki mesajı sıraya alma | M |
| **MCP istemcisi** | Yok | stdio MCP sunucularını ayar dosyasından bağlama; araçları kayda (2.1) ekleme | L |
| **Web araması** | Yok (API'de arama ucu yok) | İsteğe bağlı, yapılandırılabilir arama ucu; varsayılan kapalı | S |

### Faz 9 — Ajan kütüphanesi (alt ajan tanımları)

**Neden:** Kullanıcılar rolü 30'dan fazla kez elle yazmış ("uzman devops mühendisi", "senior full stack
developer"). Bugünkü `delegate` aracı tek bir profil çalıştırıyor: salt-okuma, rolü yok. Hedef, Claude
Code'daki alt ajan tanımları gibi, **adı, görevi, araç izinleri ve modeli belli** hazır ajanlar ve
kullanıcının kendi ajan kütüphanesi.

**Ajan tanım dosyası** (`<ad>.md`, front-matter + gövde = ajanın sistem talimatı):

```markdown
---
name: devops
description: Kubernetes, CI/CD, SSH ve altyapı işleri; teşhis ve operasyon
tools: [view_file, find_files, run_command, ssh_run, ssh_read_file, web_fetch, todo_write]
model: deepseek-v4.1-flash        # boşsa oturum modeli
thinking: false
max_steps: 15
---
Kıdemli DevOps mühendisisin. Önce salt-okuma anlık görüntü al (preset=k8s_health), ...
```

**Hazır ajanlar (varsayılan kütüphane):**

| Ajan | Görevi | Araç profili |
|---|---|---|
| `fullstack` | Özellik geliştirme, hata düzeltme, refactor; değişikliği testle doğrular | Okuma + düzenleme + `run_command` |
| `devops` | Kubernetes, Drone/Argo, SSH, altyapı teşhisi ve operasyon | Okuma + `run_command` + SSH (yazma yalnız `allow_write` host'ta) + hazır komutlar |
| `tester` | Test yazma/çalıştırma, hata yeniden üretimi, kapsam açığı bulma | Okuma + `run_command` + yalnız `tests/` altına yazma |
| `product-owner` | Gereksinimi netleştirme, kabul kriteri, iş kırılımı, plan; kod değiştirmez | Okuma + `create_plan` + `todo_write` (yazma yok) |
| `researcher` | Bugünkü `delegate` profili: kod tabanında araştırma, kısa özet | Yalnız okuma |

| # | İş | Kabul |
|---|---|---|
| 9.1 | **Ajan yükleyici:** yerleşik (`src/agents/*.md`) → kullanıcı (`~/.evren/agents/`) → proje (`.evren/agents/`); sonraki öncekini aynı adla geçersiz kılar (becerilerle aynı desen) | Bozuk dosya uyarıyla atlanır; testli |
| 9.2 | **`delegate(agent="tester", task=...)`:** alt ajan kendi talimatı, araç listesi, modeli ve adım tavanıyla, temiz bağlamda çalışır; ana geçmişe yalnız özet döner | Varsayılan ajan `researcher` (bugünkü davranış) |
| 9.3 | **Güvenlik sınırı:** ajanın araçları her zaman ana oturumun kipiyle kesişir (ask kipinde yazan ajan yazamaz); onay kapısı ve kasa aynen geçerli; alt ajan başka alt ajan çağıramaz | Kip ve özyineleme senaryo testleri |
| 9.4 | **Ana ajan rolü:** `/agent devops` oturumun ana ajanını o tanımla çalıştırır (6.7 kalıcı rolü bunun üstüne kurulur); proje ayarında varsayılan ajan | Rol bir kez seçilir |
| 9.5 | **Otomatik seçim:** görev metnine göre uygun ajanı Clef (7.3) ya da açıklama eşleşmesiyle önermek; kullanıcı onaylar ya da `@devops ...` diye açıkça çağırır | Yanlış seçimde tek tuşla geçiş |
| 9.6 | **`/agents` komutu:** listele (`[yerleşik] [kullanıcı] [proje]`), göster, `new` ile şablondan oluştur, `edit`, `reload` | Komut kaydında (6.3), testli |
| 9.7 | **Çoklu ajan iş akışı (sonraki adım):** product-owner planlar → fullstack uygular → tester doğrular; her adımın çıktısı todo listesine; adımlar arası yalnız özet taşınır | Bench'te "özellik ekle" görevinde başarı ve token, tek ajanla karşılaştırılır |

---

## 4. Sıralama ve efor

| Sıra | Faz | Efor | Risk | Not |
|---|---|---|---|---|
| 0 | **6.1 + 6.2** Yapıştırma hatası ve büyük yapıştırma | S | Düşük | Hemen yapılabilir: doğrulanmış hata + büyük token kazancı |
| 1 | **0** Ölçüm + güvenlik ağı | M | Düşük | Diğer fazların kapısı |
| 2 | **1** Bağlam boyutu (+ 7.4 özetleyici, 7.5 pencereler) | M | Orta (sıkıştırma bilgi kaybı) | En büyük kazanç; bench ile korunur |
| 3 | **7.2** Uygulayıcı modeli bench ile seçmek | S | Düşük | Yalnız bench koşusu; sonraki her faza etki eder |
| 4 | **2** Araç kaydı + modüler çekirdek (+ 6.3 komut kaydı, 7.1 model kaydı) | L | Orta (geniş refactor) | Faz 0 senaryoları olmadan başlanmaz |
| 5 | **6.4–6.7** Komut sadeleştirme, yeni komutlar, devam akışı, kalıcı rol | M | Düşük | 6.3'ten sonra |
| 6 | **8** Claude Code özellikleri: önce ayar + izin kuralları, `@dosya`, headless JSON, plan akışı; sonra hook, oturum geçmişi, yama; en son MCP | M–L | Orta | Ayar dosyası (8.1) diğerlerinin temeli |
| 6b | **9.1–9.4, 9.6** Ajan kütüphanesi: yükleyici, hazır 5 ajan, `delegate(agent=...)`, `/agent`, `/agents` | M | Orta (izin sınırı) | 8.1 ayar dosyası ve 2.1 araç kaydından sonra; 6.7 kalıcı rolün yerini alır |
| 7 | **3** Ajan davranışı, **7.3 / 7.7 / 7.8** Clef, görsel, anlamsal arama, **9.5 / 9.7** otomatik ajan seçimi ve çoklu ajan akışı | M | Orta | Her biri bench'te kazanç göstermezse kapalı kalır |
| 8 | **4 + 5** Gecikme, sağlamlık | S–M | Düşük | Paralel yürüyebilir |

**Hedef (bench ile):** istek başına prompt medyanı 64K → ≤ 25K; görev başına token %50↓; bilinen
regresyon senaryoları (8 + yapıştırma) kalıcı yeşil; döngüyle biten görev 0; uzun görevlerde başarı ≥
bugün; `/help` tek ekran ve her komut testli; Faz 8'deki "S" özelliklerin tamamı mevcut.

## 5. Kim yapsın

Bu çalışmadaki deneyim: EVREN **sınırlı, planlı özelliklerde** iyi (todo, web_fetch, git, preset),
**geniş refactor ve yeniden adlandırmalarda** zayıf (REPL değişikliği 31 istek; eski referans bıraktı).

- EVREN'e uygun: 0.3, 0.4, 1.4, 1.5, 3.2, 3.5, 4.2, 5.1, 6.5 (`/commit`, `/review`, `/init`, `/context`),
  6.7, 7.7, 9.1, 9.6, hazır ajan tanımlarının metinleri, Faz 8'deki "S" özellikler — her biri geçici
  klonda, incelenip alınır.
- Doğrudan / dar dilimlerle: 0.1, 0.2, 1.1–1.3, 6.1–6.3, 7.1–7.3, 8.1 (ayar + izinler: güvenlik sınırı),
  MCP, 9.2–9.3 (alt ajan izin sınırı), Faz 2 (önce senaryolar, sonra küçük dilimler).

## 6. Riskler

- **Prompt kısaltma davranışı bozabilir** → yalnız bench kapısıyla, madde madde.
- **Sıkıştırma özeti bilgi kaybettirebilir** → özet + son N tur ham kalır; bench'te uzun görev başarısı izlenir.
- **Bench kota tüketir** (koşu başına tahminen 1–2M token, %65+ önbellekli) → elle tetiklenir, anahtar seçimi otomatik.
- **Faz 2 geniş diff** → davranış değişikliği yasak; senaryo testleri ve bench birebir aynı kalmalı.
- **Alt ajanlar tokeni katlayabilir** (her ajan kendi bağlamını kurar) → alt ajan yalnız özet döndürür, adım tavanı tanımda zorunlu; 9.7 çoklu akış ancak bench'te tek ajandan iyiyse açılır.
- **Kullanıcı tanımlı ajan izinleri genişletebilir** → tanımdaki araç listesi oturum kipini ve onay kapısını asla aşamaz (9.3).
