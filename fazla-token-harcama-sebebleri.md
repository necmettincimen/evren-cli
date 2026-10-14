
## Kanıt sırasıyla kök nedenler

### 1. İstek başına sabit önek: ~5.412 token (ölçüldü)
```
sistem_token: 3410   araclar_token: 2002   onek_token: 5412
30_adim_onek: 162360
```
Chat API stateless: **her ajan adımı** tam sistem prompt'unu + 12 araç şemasının tamamını yeniden gönderir. `max_steps` varsayılanı **30**. Yani hiçbir iş yapılmasa bile 30 adımlık bir oturumun taban maliyeti ~162k prompt token. Bu, "tüm yeni oturumlarda" tekrarlanan tüketimin yapısal nedeni.

### 2. Compaction 128.000 token'a kadar hiç devreye girmiyor (ölçüldü)
```
DEFAULT_CONTEXT_WINDOW: 128000
model_penceresi: tanimsiz -> varsayilan   (qwen3.8-flash-next kayıtlı değil)
```
`HistoryCompactor.compact()` yalnızca `limit` aşılınca kırpıyor. 128k'ya kadar geçmiş **olduğu gibi** büyür → her adım tüm önceki araç çıktılarını da yeniden gönderir → toplam maliyet **O(n²)**. Bu oturumdaki gibi büyük `view_file`/`run_command` çıktıları biriktiğinde patlama burada.

### 3. Sistem prompt'u 1.0.0'daki kendi değişikliğimle ~%81 şişti (ölçüldü)
```
SYSTEM_PROMPT: 6571 kr  →  compose_toplam: 11937 kr   (ek_yuk: 5366 kr)
docs_blok: 4913 kr   cli_docs: 0   exec_ctx: 450 kr
```
`docs_context_block` = **4.913 kr** — 1.0.0'da eklediğim `agent.md` enjeksiyonu. Tahmini **+~1.400 token/istek** ≈ 30 adımda **+42k token**. Sorunuzun zamanlaması bu özellikle örtüşüyor; dürüst kök neden bu.

### 4. Her kullanıcı girdisi başına gizli ek API çağrısı (ölçüldü)
```
guidance_system_kr: 1870   (+ guidance talimatı + araç şeması)
```
`repl.py:726 → _collect_frame → request_frame_guidance`: ana ajan turundan **bağımsız**, sistem + şema taşıyan tam bir istek. Netleştirme turu açılırsa **2.** çağrı. Yani siz tek satır yazmadan önce ~4.500 token harcanıyor.

### 5. Stall katlaması: çıktı bütçesi 8×'e kadar büyüyor (kaynakta doğrulandı)
```
STALL_CAP: 32768   MAX_STALLS: 4   EVREN_DEFAULT_MAX_TOKENS: 4096
```
`output_budget = min(output_budget * 2**repeats, 32768)`. Reasoning modeli kesildiğinde her yeniden deneme tüm bağlamı tekrar gönderir ve bütçe 4096→8192→16384→32768 katlanır.

### 6. Ölçüm/loğun eksikliği (bulgu)
`.evren/logs` içinde yalnızca `update.log` (18 KB) ve `ssh_audit.log` (450 B) var — **API kullanım logu yok**. `last_usage` yalnızca bellekte; `/tokens` komutu anlık gösteriyor ama kalıcı kayıt tutulmuyor. Bu yüzden tüketim ancak canlı `/tokens` ile görülebiliyor, geçmişe dönük analiz yapılamıyor.

## Doğrulanmış hipotez sıralaması

| # | Neden | Etki | Kanıt |
|---|---|---|---|
| 1 | Compaction eşiği 128k + model penceresi tanımsız | **Baskın** (uzun oturum) | `DEFAULT_CONTEXT_WINDOW`, `compact()` kaynağı |
| 2 | Sabit önek × 30 adım | Yüksek, her oturumda sabit | 5.412 token ölçümü |
| 3 | `agent.md` enjeksiyonu (1.0.0) | Orta (+%26 sistem yükü) | 4.913 kr ölçümü |
| 4 | Prompt-hazırlama çağrıları | Girdi başına +1~2 istek | `repl.py:726`, 1.870 kr |
| 5 | Stall katlaması | Aralıklı, tek seferde büyük | `_STALL_TOKEN_CAP` |

## Güvenli düzeltme önerileri (henüz uygulanmadı)

**Onaylayacağınız minimum adım (tek dosya, geri alınabilir):**
`src/token_manager.py` içinde `MODEL_CONTEXT_WINDOWS` sözlüğüne `qwen3.8-flash-next` için gerçek pencereyi girip compaction'ı **erken** devreye sokmak — ya da `TokenManager`'a `compact_at_ratio` (~0.6) ekleyip `compact()` çağrısında `max_tokens=int(pencere*ratio)` geçmek. Etki: 1 numaralı baskın nedeni keser, davranış/kod sözleşmesi değişmez.

**İkinci kademe (ayrı onay):** `EVREN_DOCS_TOKENS` varsayılanını 3500 → 1200 çekmek (3 numarayı azaltır; tek satır, env ile zaten ayarlanabiliyor).

**Üçüncü kademe (davranış değişikliği, ayrı tartışma):** guidance çağrısını yalnızca alan eksikse tek isteğe indirmek; araç şemalarını mod bazında süzmek (`ask`/`plan`'da `ssh_write_file` gönderme).

**Doğrulama planı:** değişiklik öncesi/sonrası aynı 3 adımlık sentetik görevi `MagicMock` yerine gerçek istek kaydediciyle koş, `token_report()['estimated_prompt_tokens']` ve sunucu `usage.prompt_tokens` farkını karşılaştır; `python -m pytest -q` tam yeşil kalmalı (şu an taban: 488 passed).

**Hemen sıfır riskli kazanç (değişiklik gerektirmez):** `evren agent --max-steps 12` ve `set EVREN_DOCS_TOKENS=1200`.

Hangi kademi uygulayayım? 1. kademe için onay verirseniz tek dosyada cerrahi değişiklik + test koşuyorum.