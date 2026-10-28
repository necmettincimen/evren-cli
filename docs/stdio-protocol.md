# `evren --stdio` protokolü (v1)

Editör eklentileri (VS Code) için uzun ömürlü kip. Eklenti LLM'e bağlanmaz; ajan
mantığı CLI'da kalır, eklenti süreçle stdio üzerinden konuşur.

```
evren --stdio [--workspace <yol>] [--model <ad>] [--continue] [--yes] [--allowed-tools edit_file,run_command]
```

- **stdin**: satır başına bir JSON komutu (NDJSON, UTF-8).
- **stdout**: satır başına bir JSON olayı. stdout **yalnız protokol** içindir.
- **stderr**: CLI'nın diğer bütün çıktısı (rich, print, alt süreçler). Eklenti bunu log kanalına yazar.
- stdin kapanınca (EOF) süreç çalışan turu iptal edip `0` ile çıkar.
- `--yes` tüm onayları otomatik verir; `--allowed-tools` listedeki araçları onaysız çalıştırır
  (başsız kiple aynı anlam). `.evren/settings*.json` izin kuralları da geçerlidir.

Makine tarafından okunur sözleşme: [`protocol/stdio-v1.schema.json`](../protocol/stdio-v1.schema.json)
(JSON Schema 2020-12). CLI testleri gerçek çıktıyı bu şemayla doğrular; evren-vscode CI'ı aynı
dosyayı CLI etiketinden indirip istemci tarafını sınar. Geriye uyumsuz değişiklik protokol
sürümünü artırır (`stdio-v2.schema.json`).

## Zarf

Her mesajda bulunur:

| alan | anlam |
|---|---|
| `v` | şema sürümü, şimdilik `1`. Farklı `v` → `error{code:"unsupported_version"}` |
| `type` | komut ya da olay adı |
| `id` | komutta istemcinin seçtiği kimlik (isteğe bağlı); olayda sunucunun ürettiği `s<N>` |
| `reply_to` | (yalnız olay) olaya yol açan komutun `id`'si; varsa |

Bilinmeyen alanlar yok sayılır. İleriye uyumluluk için istemci tanımadığı olay tiplerini atlamalıdır.

## Komutlar (eklenti → CLI)

| type | alanlar | not |
|---|---|---|
| `prompt` | `text`, `mode?` (`normal`\|`plan`\|`ask`, yalnız bu tur), `attachments?: [{path, range?: {start, end}}]` | Tur ayrı thread'de başlar. Çalışırken ikinci `prompt` → `error{code:"busy"}` |
| `cancel` | — | Çalışan turu durdurur, bekleyen onay/soruları reddeder. Sonunda `turn_end` gelir |
| `approval_response` | `request_id`, `decision`: `allow`\|`deny`\|`always`, `edited_content?` | `always`: `.evren/settings.local.json`'a izin kuralı ekler. `edited_content` (yalnız dosya değişikliği): diske bu içerik yazılır |
| `ask_user_response` | `request_id`, `answer` | |
| `set_model` | `model` | → `model_changed{model}` |
| `set_mode` | `mode` | kalıcı kip → `mode_changed{mode}` |
| `list_sessions` | `limit?` | → `sessions{items:[{session_id, title, model, mode}]}` |
| `resume` | `session_id` | → `session_loaded{session_id, messages:[{role, text}]}` |
| `new_session` | — | → `session_loaded{session_id:null, messages:[]}` |
| `list_models` | — | → `models{models, current}` |
| `quota` | — | → `quota{quota}` (maskeli) |
| `slash` | `command`, `args?` | Güvenli alt küme: `model, mode, new, clear, resume, sessions, models, quota, tokens, todos`. Diğerleri → `error{code:"unsupported_slash"}` |

`set_model`, `set_mode`, `resume`, `new_session` tur çalışırken `busy` hatası verir.
Ek dosyalar isteğe `@yol:10-20` biçiminde eklenir; metindeki `@yol#L10-20` de tanınır.

## Olaylar (CLI → eklenti)

| type | alanlar |
|---|---|
| `ready` | `version`, `model`, `workspace`, `protocol` — süreç başlayınca bir kez |
| `turn_start` | (`reply_to` = prompt id) |
| `assistant_delta` | `text` — akan yanıt parçası (markdown) |
| `reasoning_delta` | `text` — akan düşünme parçası |
| `tool_call` | `call_id`, `name`, `args` (maskeli) |
| `tool_result` | `call_id`, `name`, `ok`, `summary` (≤ 4 KB, maskeli), `output_truncated` |
| `file_change_proposed` | `request_id`, `path`, `old_content`, `new_content` — yazmadan önce; ardından aynı `request_id` ile `approval_request` gelir |
| `approval_request` | `request_id`, `kind`: `edit`\|`command`\|`ssh`\|`other`, `title`, `detail` |
| `ask_user` | `request_id`, `question`, `options?` (evet/hayır sorularında `["evet","hayır"]`) |
| `todos` | `items: [{content, status}]` — `todo_write` sonrası |
| `usage` | `prompt_tokens`, `completion_tokens` (oturum toplamı) — her tur sonunda |
| `turn_end` | `result`, `changed_files` (git'e göre bu turda değişenler), `exit_code` (0/1) |
| `error` | `message`, `code` |

Hata kodları: `invalid_json`, `invalid_message`, `unsupported_version`, `unknown_command`,
`busy`, `empty_prompt`, `invalid_decision`, `unknown_request`, `invalid_mode`, `invalid_model`,
`unknown_session`, `unsupported_slash`, `mention`, `quota`, `internal`.

### Sıra garantileri

- Bir turda: `turn_start` → (`reasoning_delta`|`assistant_delta`|`tool_call`|…)* → `usage` → `turn_end`.
- Bir araç için: `tool_call` → [`file_change_proposed`] → [`approval_request`] → `tool_result`.
- Onay bekleyen araç, yanıt gelene ya da `cancel` gelene kadar bekler. İptalde onay `deny` sayılır.

### Maskeleme

Araç argümanları, `tool_result.summary`, onay başlık/ayrıntısı ve kota oturum kasasıyla
(`secret_vault`) maskelenir. `file_change_proposed` içerikleri **maskelenmez**: eklenti
düzenlenmiş kopyayı geri gönderebilir ve o kopya diske aynen yazılır.

## Örnek

```
→ {"v":1,"type":"prompt","id":"p1","text":"README'ye bir satır ekle"}
← {"v":1,"type":"turn_start","id":"s2","reply_to":"p1"}
← {"v":1,"type":"assistant_delta","id":"s3","text":"README güncelleniyor."}
← {"v":1,"type":"tool_call","id":"s4","call_id":"call_1","name":"edit_file","args":{...}}
← {"v":1,"type":"file_change_proposed","id":"s6","request_id":"r5","path":"README.md","old_content":"…","new_content":"…"}
← {"v":1,"type":"approval_request","id":"s7","request_id":"r5","kind":"edit","title":"…","detail":"README.md"}
→ {"v":1,"type":"approval_response","request_id":"r5","decision":"allow"}
← {"v":1,"type":"tool_result","id":"s8","call_id":"call_1","name":"edit_file","ok":true,"summary":"BAŞARILI: …","output_truncated":false}
← {"v":1,"type":"usage","id":"s10","reply_to":"p1","prompt_tokens":1200,"completion_tokens":80}
← {"v":1,"type":"turn_end","id":"s11","reply_to":"p1","result":"…","changed_files":["README.md"],"exit_code":0}
```

## Sürüm uyumu

`ready.version` ≥ 3.10.0 olan CLI bu protokolü (`protocol: 1`) destekler. Geriye uyumsuz
değişiklikte `v` artar; alan eklemek uyumlu değişiklik sayılır.
