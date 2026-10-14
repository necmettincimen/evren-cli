# EVREN CLI — Aşırı Token Tüketimi Güncelleme Planı

**Kaynak bulgular:** `fazla-token-harcama-sebebleri.md` (repo kökü, 63 satır)
**Kapsam:** yalnızca `D:\Github\evren-cli` · yerel Windows/cmd · SSH/uzak sistem yok
**Bu planın durumu:** öneri. **Hiçbir kod, yapılandırma veya bağımlılık değişikliği yapılmadı.**
Mimari kararlar §7'de onaya bırakılmıştır.

---

## 0. Ortam Notu (uygulamadan önce okuyun)

İki gerçek, bu planın yazımı sırasında canlı doğrulandı:

1. **`plans/` klasörü çalışma alanında silinmiş durumda.** `git status` iki kayıtlı dosyayı
   silinmiş gösteriyor: `plans/smart-context-docs/plan.md`, `plans/ssh-remote-ops/plan.md`.
   İçerikler `38140e6` commit'inde duruyor; `git checkout -- plans/` ile geri getirilebilir.
   Bu plan **yeni** bir alt klasöre yazıldığı için silinmiş dosyaların üzerine yazmıyor.
2. **`MODEL_CONTEXT_WINDOWS` adında bir sözlük `src/token_manager.py` içinde YOK.**
   Kaynak bulgunun §53'deki "MODEL_CONTEXT_WINDOWS sözlüğüne gir" önerisi **geçersizdir**;
   bu plan F1'de gerçek API'ye (`TokenManager(context_window=...)` + `compact(max_tokens=...)`)
   dayanan doğru yolu tarif eder. Doğrulanan modül sabitleri:
   `DEFAULT_CONTEXT_WINDOW`, `DEFAULT_CHARS_PER_TOKEN`, `REASONING_MODELS`,
   `REASONING_MIN_OUTPUT_TOKENS`, `QWEN_FLASH_MIN_OUTPUT_TOKENS`.

---

## 1. Doğrulanmış Ölçüm Tablosu (taban çizgisi)

| Ölçüm | Değer | Nereden |
|---|---|---|
| Sistem prompt'u (ham) | 6.571 kr | `src/agent.py::SYSTEM_PROMPT` |
| `_compose_system` toplamı | **11.937 kr** | canlı `AgentSession.system_prompt` |
| Eklenen bağlam yükü | 5.366 kr | fark |
| — `docs_context_block` | **4.913 kr** | `src/project_docs.py` |
| — `execution_context_block` | 450 kr | `src/config.py` |
| — `cli_docs_context_block` | 0 kr (bu repoda) | `src/cli_home.py` |
| Araç şeması JSON | **7.007 kr / 12 araç** | `src/tools.py::TOOLS_SCHEMA` |
| En büyük 3 araç | `edit_file` 1.223 · `run_command` 750 · `ssh_run` 742 | yukarı |
| **İstek başına sabit önek** | **5.412 token** | `TokenManager.estimate_*` |
| 30 adımlık taban maliyet | **162.360 prompt token** | 5.412 × 30 |
| Compaction eşiği | **128.000 token** | `DEFAULT_CONTEXT_WINDOW` |
| Varsayılan çıktı bütçesi | 4.096 | `EVREN_DEFAULT_MAX_TOKENS` |
| Stall tavanı / eşiği | 32.768 / 4 | `_STALL_TOKEN_CAP` / `_MAX_STALLS` |
| `max_steps` varsayılanı | **30** | `AgentSession.run_step` |
| Varsayılan model | `qwen3.8-flash-next` | `EVREN_DEFAULT_MODEL` |
| Model reasoning mi? | **EVET** | `REASONING_MODELS` kümesinde |
| Test tabanı | **488 passed, 55 subtests** | `python -m pytest -q` |

**Yapısal neden:** Chat API stateless. `agent.py:605` her turda `compact()` çağırır,
`agent.py:669` her turda `tools=TOOLS_SCHEMA` gönderir. Compaction 128k'ya kadar hiçbir
şey yapmadığı için geçmiş **olduğu gibi** birikir → toplam maliyet **O(n²)**.

---

## 2. Öncelik Sırası (etki × risk)

| Öncelik | Bulgu | Beklenen etki | Risk | Onay |
|---|---|---|---|---|
| **P0** | F6 — `max_steps` varsayılanı | taban 162k → 65k (**−%60**) | Düşük | Gerekli |
| **P1** | F1 — compaction eşiği 128k | uzun oturumda **%30–60** | Düşük | Gerekli |
| **P2** | F3 — `docs_context_block` 4.913 kr | −~900 token/istek (**−27k**/30 adım) | Düşük | Gerekli |
| **P3** | F4 — guidance çağrısı başına +1–2 istek | girdi başına **−~4.500 token** | Orta | Gerekli |
| **P4** | F2 — 12 araç şeması her istekte | −~500–700 token/istek | Orta | Gerekli |
| **P5** | F5 — stall bütçesi 8× katlama | stall anında **−%75** çıktı | Orta | Gerekli |
| **P6** | F7 — API kullanım logu yok | doğrudan tasarruf yok, **ölçülebilirlik** | Düşük | Gerekli |

**P0–P2 birlikte** 30 adımlık tipik bir oturumda tahmini **%45–65** azalma hedefler
(§8'de ölçüm yöntemiyle doğrulanmadan "kesin" sayılmaz).

---

## 3. Bulgu Bazlı Güncelleme Maddeleri

### F1 — Compaction 128.000 token'a kadar hiç devreye girmiyor · **P1**

**Sorun.** `HistoryCompactor.compact()` yalnızca `limit` aşılınca kırpıyor ve `limit` =
`TokenManager.context_window` = `DEFAULT_CONTEXT_WINDOW` = 128.000. Varsayılan modelin
gerçek penceresi için özel kayıt yok. Sonuç: 128k'ya kadar her adım tüm geçmiş + tüm
araç çıktılarını yeniden gönderiyor; maliyet O(n²).

**Önerilen güncelleme (davranış sözleşmesi değişmez, yalnızca eşik):**
1. `src/token_manager.py` içine **yeni** bir sabit sözlük ekle:
   `MODEL_CONTEXT_WINDOWS = {"qwen3.8-flash-next": <gerçek pencere>, ...}`
   ve `TokenManager.resolve_context_window(model)` yardımcı metodu
   (bilinmeyen model → `DEFAULT_CONTEXT_WINDOW`).
2. `TokenManager.__init__` mevcut `context_window` parametresini korusun
   (geriye dönük uyum); yeni çözümleme **yalnızca** `AgentSession` kurulumunda kullanılsın.
3. `src/agent.py:605` çağrısında `max_tokens=int(pencere * COMPACT_AT_RATIO)` geç;
   `COMPACT_AT_RATIO` varsayılan **0.6**, `EVREN_COMPACT_RATIO` env ile ayarlanabilir.
4. Erken kırpma tetiklendiğinde tek satır bilgi bas (`print_info`), kullanıcı şaşırmasın.

> **Dikkat:** `<gerçek pencere>` değerini **sen doğrulayacaksın** — EVREN API model
> listesi/`/models` yanıtından teyit edilmeden sayı uydurmayacağım. Doğrulama §8-A.

**Beklenen etki.** Uzun oturumlarda (≥15 adım) prompt token **%30–60** azalma; kısa
oturumlarda değişiklik yok (eşik hiç aşılmıyor). En büyük tekil kazanç.

**Doğrulama.**
- Birim: `TokenManager.resolve_context_window` bilinmeyen/bilinen model; `compact()`
  `max_tokens` override ile Stage 1/2/3'e giriyor.
- Entegrasyon: 25 adımlık sentetik görevde `token_report()['estimated_prompt_tokens']`
  öncesi/sonrası karşılaştırması; `usage.prompt_tokens` toplamı düşmeli.
- Regresyon: `tests/test_history_compactor.py` + tam paket (taban 488).

**Risk.** Erken kırpma, modelin ihtiyaç duyduğu bir araç çıktısını düşürebilir → görev
kalitesi düşer. **Azaltma:** Stage 1 yalnızca *büyük* araç çıktılarını kırpıyor (mevcut),
`compact()` atomik stage'lerle çalışıyor, son tur korunuyor; ratio env ile 0.8'e çekilebilir.

---

### F2 — 12 araç şemasının tamamı her istekte gidiyor (7.007 kr) · **P4**

**Sorun.** `agent.py:669` her turda `tools=TOOLS_SCHEMA` gönderiyor. `ask` ve `plan`
kiplerinde yazma araçları (`edit_file`, `write_file`, `ssh_write_file`) ve SSH araçları
gönderilse de gateway tarafından reddediliyor — yani **boşuna** ~2.500 kr taşınıyor.

**Önerilen güncelleme.**
1. `src/tools.py` içine `tools_for_mode(mode) -> list[dict]` saf fonksiyonu ekle
   (mevcut `TOOLS_SCHEMA` **değişmeden** korunur).
2. `agent.py` istek gövdesinde `tools=tools_for_mode(effective_mode)` kullan.
3. Kural: `ask` → salt-okuma araçları + `ask_user`; `plan` → salt-okuma + `create_plan`;
   `normal` → tam liste (bugünkü davranış).
4. Gateway (`tool_gateway.py`) **olduğu gibi kalır** — çift katman koruma.

**Beklenen etki.** `ask`/`plan` oturumlarında istek başına **−~500–700 token**;
`normal` modda değişiklik yok.

**Doğrulama.**
- Birim: her mod için beklenen araç adı kümesi; `normal` kümesi `TOOLS_SCHEMA` ile birebir.
- Sözleşme: `ask` modunda `edit_file` şemada **yokken** model çağırırsa mevcut gateway
  red yolunun çalıştığı testi.
- Tam paket yeşil (taban 488).

**Risk.** Model şemada olmayan aracı çağırmaya çalışabilir → red + tur kaybı.
**Azaltma:** sistem prompt'undaki kip açıklaması zaten kısıtı söylüyor; red mesajı
yönlendirici. `normal` mod hiç etkilenmez.

---

### F3 — Sistem prompt'u 1.0.0'daki docs enjeksiyonuyla %81 şişti · **P2**

**Sorun.** `docs_context_block` = **4.913 kr** (sistem 6.571 → 11.937 kr). Tahmini
**+~1.400 token/istek** ≈ 30 adımda **+42k token**. Bu, 1.0.0'da eklenen `agent.md`
özelliğinin yan etkisi.

**Önerilen güncelleme (özelliği kaldırmadan).**
1. `EVREN_DOCS_TOKENS` varsayılanını **3500 → 1200** çek (`src/project_docs.py`).
2. `agent_md_context_block` içindeki bölüm sıralamasını sinyal başına yap:
   `conventions` + `current_focus` önce, `project_map` (en hacimli, en bayat) **yalnızca
   bütçe kalırsa**. `project_map` zaten `docs_digest`te özetleniyor.
3. `runtime_env` küçültülebilir: dosya ağacı satırlarını `MAX_MAP_LINES` 60 → 25.
4. Açılışta bir kez, oturum boyunca **sistem prompt'unu dondur**: şu an
   `refresh_context()` her çağrıda bloğu yeniden okuyor; güncelleme yalnızca
   `/docs refresh` ile olsun (bayat veri + tekrar maliyeti yerine bilinçli tazeleme).

**Beklenen etki.** İstek başına **−~900 token** → 30 adımda **−~27k token**.
Özellik işlevselliği korunur (bağlam duruyor, sadece daha kısa).

**Doğrulama.**
- `tests/test_project_docs.py` mevcut 32 test + yeni: bütçe 1200 iken `conventions`
  ve `current_focus` blokte, `project_map` kırpılıyor, "kırpıldı" uyarısı basılıyor.
- `tests/test_docs_context.py` 16 test: enjeksiyon hâlâ sistem prompt'unda.
- Canlı: `python -c` ile `len(session.system_prompt)` ölçümü (hedef ≤ 8.500 kr).

**Risk.** Ajan proje haritasını göremez → gereksiz `list_directory`/`view_file` turu
artabilir (net tasarrufu azaltır). **Azaltma:** `docs_digest` özet haritayı koruyor;
ratio env ile eski değere döndürülebilir.

---

### F4 — Her kullanıcı girdisi başına gizli ek API çağrısı · **P3**

**Sorun.** `repl.py:726 → _collect_frame → request_frame_guidance`: ana ajan turundan
bağımsız, sistem + araç şeması taşıyan tam bir istek (`guidance_system_kr` 1.870 +
talimat + şema ≈ **4.500 token**). Netleştirme turu açılırsa **2.** çağrı.

**Önerilen güncelleme.**
1. Guidance çağrısını **yalnızca gerçekten boş alan varsa** yap
   (`frame.is_empty()` / `missing` kümesi boşsa hiç istek atma).
2. Guidance isteğine **araç şemasını gönderme** — yanıt zaten tek bir araç çağrısı
   şeması istiyor; tek araçlık şema gönder (7.007 kr → ~300 kr).
3. Guidance sistem mesajını ana `SYSTEM_PROMPT`'tan **bağımsız kısa** bir talimatla
   kur (mevcut `instruction` zaten kısa; `execution_context` + `_docs_context`
   yerine yalnızca `docs_digest` tek satırı).
4. Aynı brief için sonucu **oturum boyunca önbelleğe al** (değişmediyse tekrar sorma).

**Beklenen etki.** Girdi başına **−~4.500 token** (çağrı tamamen atlandığında) veya
**−~3.500 token** (tek araç şemasıyla). Netleştirme turu olan girdilerde **−1 tam istek**.

**Doğrulama.**
- `tests/test_prompt_frame.py`: boş alan yokken `request_frame_guidance` **hiç çağrılmıyor**
  (MagicMock `assert_not_called`).
- Şema boyutu testi: guidance isteğine giden `tools` uzunluğu ≤ 1.
- Cache testi: aynı brief ikinci kez guidance tetiklemiyor.
- Mevcut REPL akış testleri kırılmamalı.

**Risk.** Zayıf brief'ler için öneri/alan doldurma davranışı azalır — bu bir **ürün
kararı**, §7'de onay isteniyor. **Azaltma:** `/frame` komutuyla elle tetikleme yolu kalsın.

---

### F5 — Stall durumunda çıktı bütçesi 8× katlanıyor · **P5**

**Sorun.** `output_budget = min(output_budget * 2**repeats, _STALL_TOKEN_CAP)`;
`_STALL_TOKEN_CAP = 32768`, `_MAX_STALLS = 4`. Varsayılan model `REASONING_MODELS`
içinde: thinking + yanıt **aynı** bütçeyi paylaşıyor, yani 32.768'lik tavan büyük ölçüde
**gizli reasoning**'e gidiyor. Her yeniden deneme tüm bağlamı tekrar gönderiyor.

**Önerilen güncelleme.**
1. `_STALL_TOKEN_CAP` **32768 → 8192** (veya `REASONING_MIN_OUTPUT_TOKENS` ile ilişkili
   bir tavana bağla).
2. Katlama yerine **önce bağlamı küçült**: 2. stall'da `compact()` çağrısını agresif
   `max_tokens` ile zorla, sonra bütçeyi artır.
3. 3. stall'da döngüyü kırıp kullanıcıya "bağlam çok büyük, `/compact` veya yeni oturum"
   öner (mevcut `_MAX_STALLS` mesajını zenginleştir).

**Beklenen etki.** Stall anında çıktı bütçesi **−%75**; gizli reasoning token'ları
azalır. Prompt tarafı değişmez (hatta 2. madde ile düşer).

**Doğrulama.**
- `tests/test_agent.py` mevcut stall testleri (kesinti → devam) geçmeli.
- Yeni: 2 stall sonrası `compact()`'in daha düşük `max_tokens` ile çağrıldığı;
  4 stall'da hata mesajının `/compact` önerdiği.
- `resolve_output_budget` tavan testi.

**Risk.** Gerçekten uzun çıktı üreten meşru görevler kesilebilir → devam turu sayısı
artabilir (net etki negatife dönebilir). **Azaltma:** tavan env ile
(`EVREN_STALL_TOKEN_CAP`) yükseltilebilir; reasoning modeli için ayrı değer tutulur.

---

### F6 — `max_steps` varsayılanı 30 · **P0**

**Sorun.** Sabit önek 5.412 token × 30 adım = **162.360 token taban maliyet** — iş
yapılmasa bile. Bu, "tüm yeni oturumlarda" tekrarlanan tüketimin en ucuz kesilebilir yeri.

**Önerilen güncelleme.**
1. `AgentSession.run_step(max_steps=30)` varsayılanını **12**'ye çek;
   `evren agent --max-steps` ve REPL'de `/steps <n>` ile yükseltilebilir kalsın.
2. Adım sınırına yaklaşınca **uyar**: son 3 adımda modele "kapsamlı özet ver, işi
   bitir" yönlendirmesi ekle (mevcut stall yönlendirmesiyle aynı mekanizma).
3. Sınır aşımında bugünkü gibi geçmişi koru + `/continue` öner (mevcut davranış).

**Beklenen etki.** Taban **162k → 65k (−%60)**. Uzun görevler için kullanıcı açıkça
yükseltir.

**Doğrulama.**
- `tests/test_agent.py`: `max_steps` varsayılanı 12; sınır aşımı mesajı `/continue` içeriyor.
- CLI parser testi: `--max-steps` hâlâ çalışıyor, `min=1` doğrulaması duruyor.
- Tam paket yeşil.

**Risk.** 12 adımın yetmediği gerçek görevler yarıda kalır → kullanıcı `/continue`
yazmak zorunda kalır (sürtünme). **Azaltma:** uyarı + tek komutla devam; env varsayılanı
(`EVREN_MAX_STEPS`) ayarlanabilir.

---

### F7 — API kullanım logu yok (ölçemiyorsun) · **P6**

**Sorun.** `.evren/logs` içinde yalnızca `update.log` (18 KB) ve `ssh_audit.log` (450 B).
`last_usage` yalnızca bellekte; `/tokens` anlık gösteriyor, kalıcı kayıt yok. Geçmişe
dönük analiz yapılamıyor — bu planın sayıları da ancak canlı ölçümle doğrulanabiliyor.

**Önerilen güncelleme.**
1. Her API yanıtından sonra `.evren/logs/usage.jsonl` satırı ekle:
   `{ts, model, prompt_tokens, completion_tokens, total_tokens, step, mode}`.
   **Yalnızca sayaç** — mesaj içeriği, araç argümanı, dosya yolu, anahtar yazılmaz.
2. `/tokens` çıktısına oturum toplamı + son 10 isteğin ortalamasını ekle.
3. `evren usage [--days N]` ile günlük toplam (opsiyonel, ayrı onay).
4. Log `.evren/.gitignore` içinde zaten (`logs/`) — sızıntı riski yok.

**Beklenen etki.** Doğrudan token tasarrufu **yok**; ancak F1–F6 etkisinin **ölçülebilir**
hâle gelmesi ve sapmanın görünür olması. Bu olmadan diğer maddelerin doğrulaması zayıf kalır.

**Doğrulama.**
- Birim: sahte `usage` ile satır yazılıyor; alan kümesi tam; **içerik alanı yok**.
- Sır testi: satırda `evren_llm_` geçmediğini doğrulayan assert.
- Yazma hatası (dosya kilitli) akışı **bozmamalı** (best-effort, `try/except`).

**Risk.** Disk kullanımı (satır başına ~150 B, ihmal edilebilir); JSONL büyürse
rotasyon gerekir. **Azaltma:** 5.000 satırda bir `.1` arşivi.

---

## 4. Uygulama Sırası (her faz ayrı commit, ayrı onay)

| # | Faz | Dosyalar | Taban test |
|---|---|---|---|
| 0 | Ölçüm altyapısı (**F7**) | `api_client.py`, `agent.py`, `config.py` | 488 → +3 |
| 1 | **F6** `max_steps` 12 | `agent.py`, `cli.py` | +2 |
| 2 | **F1** compaction eşiği | `token_manager.py`, `agent.py` | +4 |
| 3 | **F3** docs bütçesi 1200 | `project_docs.py` | +2 |
| 4 | **F2** mod bazlı araç şeması | `tools.py`, `agent.py` | +3 |
| 5 | **F4** guidance koşullu + tek şema | `prompt_frame.py`, `repl.py` | +3 |
| 6 | **F5** stall tavanı | `agent.py` | +2 |

Her faz sonunda: `python -m compileall -q src && python -m pytest -q` (yeşil zorunlu)
ve §8 ölçüm protokolü ile önceki fazla karşılaştırma.

---

## 5. Sıfır Riskli Geçici Çözümler (kod değişikliği gerektirmez)

Onay beklerken hemen kullanılabilir:

```bat
set EVREN_DOCS_TOKENS=1200
evren agent --max-steps 12 "görevin"
```
REPL içinde: `/tokens` ile adım başı artışı izle; `/mode ask` ile yazma araçlarını
kullanmadan keşif yap.

---

## 6. Doğrulama Protokolü

**A. Model penceresini doğrula (F1 öncesi zorunlu).**
```bat
python -m src.cli models
```
`qwen3.8-flash-next` için gerçek bağlam penceresini API yanıtından oku. Yanıtta pencere
alanı yoksa **F1'de sayı uydurmayacağım** — yalnızca `COMPACT_AT_RATIO` ile
`DEFAULT_CONTEXT_WINDOW` üzerinden ilerlenecek ve bu açıkça raporlanacak.

**B. Öncesi/sonrası istek yakalama.**
`MagicMock` yerine kaydedici sahte istemci: her `chat_stream_assembled` çağrısında
`len(messages)` + `len(json.dumps(tools))` + tahmini token. Aynı 3 adımlık sentetik
görevi her fazın öncesi/sonrası koş; `tests/helpers.py` kalıbını kullan.

**C. Sunucu gerçekliği ile kalibrasyon.**
`usage.prompt_tokens` (sunucu) ile `estimate_messages` (tahmin) farkını F7 logundan
karşılaştır; tahminci sapması %20'yi aşarsa `TokenManager.calibrate_from_response`
yolunu gözden geçir (bu plan kapsamı dışında, ayrı bulgu olarak raporlanır).

**D. Kabul ölçütü.**
- 30 adımlık sentetik görevde toplam `prompt_tokens` **en az %35** düşmeli.
- `python -m pytest -q` tam yeşil (taban 488, her fazda yeni testlerle artar).
- `git status` temiz; üretilen bağlam dosyaları commit'e girmemeli.

---

## 7. Onay Gereken Mimari Kararlar (kesinleştirilmedi)

| # | Karar | Seçenekler | Etkisi |
|---|---|---|---|
| K1 | `max_steps` varsayılanı | 12 / 16 / 20 / 30 (mevcut) | Taban maliyet vs. görev tamamlama |
| K2 | `COMPACT_AT_RATIO` | 0.5 / 0.6 / 0.75 | Tasarruf vs. bağlam kaybı |
| K3 | `EVREN_DOCS_TOKENS` | 1200 / 2000 / 3500 (mevcut) | Bağlam zenginliği vs. maliyet |
| K4 | Guidance'ı koşullu yapma | tamamen koşullu / her zaman / tek şema ile | Ürün davranışı değişir |
| K5 | Mod bazlı araç süzme | evet / hayır | `ask`/`plan` sadeliği vs. esneklik |
| K6 | Stall tavanı | 8192 / 16384 / 32768 (mevcut) | Kesinti riski vs. maliyet |
| K7 | Model penceresi kaydı | API'den doğrulanırsa ekle / hiç ekleme | F1'in doğruluğu |

Hiçbiri bu planda **karara bağlanmadı**; uygulama öncesi tek tek sorulacak.

---

## 8. Riskler ve Geri Alma

| Risk | Etki | Azaltma / Geri alma |
|---|---|---|
| Erken compaction bağlam kaybettirir, görev kalitesi düşer | Orta | Ratio env ile anında eski değere; stage'ler son turu koruyor |
| `max_steps=12` görevleri yarıda bırakır | Orta | `/continue` + adım uyarısı + env varsayılanı |
| Araç süzme → model olmayan aracı çağırır | Düşük | Gateway red yolu zaten var; `normal` mod etkilenmez |
| Guidance azalması brief kalitesini düşürür | Orta | Elle tetikleme yolu; K4 onayı |
| Stall tavanı meşru uzun çıktıları keser | Orta | `EVREN_STALL_TOKEN_CAP`; reasoning için ayrı değer |
| Uydurulmuş model penceresi F1'i yanlış kurar | **Yüksek** | §6-A zorunlu; doğrulanamazsa sayı **yazılmayacak** |
| F7 loguna yanlışlıkla içerik yazılır | **Yüksek** | Yalnızca sayaç alanları; sır assert'i; `logs/` zaten `.gitignore`'da |
| `plans/` silinmiş durumuyla çakışma | Düşük | Bu plan yeni alt klasörde; `git checkout -- plans/` ile diğerleri geri gelir |

**Geri alma:** her faz tek commit → `git revert <hash>`. Dosya düzeyinde
`evren rollback <dosya>` (`.evren/backups/`). Tüm env anahtarları eski davranışa döndürür.

---

## 9. Kapsam Dışı

- `plans/` klasörünün silinmiş dosyalarını geri getirme (kullanıcı kararı)
- Sunucu tarafı model/fiyatlandırma değişiklikleri
- Tahminci (`DEFAULT_CHARS_PER_TOKEN = 3.5`) kalibrasyonunun yeniden yazılması
  — §6-C'de ayrı bulgu olarak raporlanacak
- Uzak host / SSH kimlik doğrulaması ve `token.txt` konusu
  (bkz. `plans/token-txt-auth-diagnosis/plan.md`)
- Yeni özellik/mimari (araç ekleme, çoklu oturum, vektör arama vb.)

---

## 10. Özet

En büyük üç kazanç: **F1** (compaction eşiği — O(n²) büyümenin kesilmesi),
**F6** (`max_steps` 30→12 — taban maliyetin %60 düşmesi), **F3** (docs bloğu
4.913 kr → ~1.200 token bütçesi). **F7** bunları ölçülebilir kıldığı için **sıfırıncı
faz** olarak en başta geliyor. Hiçbir madde uygulanmadı; §7'deki 7 mimari karar onay
bekliyor.
