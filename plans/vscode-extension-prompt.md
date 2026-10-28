# Prompt: EVREN CLI için VS Code eklentisi (Claude Code benzeri)

> Bu metni yeni bir oturuma olduğu gibi ver. İki repo var: `D:\Github\evren-cli` (Python, mevcut) ve yeni `D:\Github\evren-vscode` (TypeScript eklentisi).

---

## Rol ve hedef

Sen kıdemli bir TypeScript + Python geliştiricisisin. EVREN CLI (`D:\Github\evren-cli`, Python ≥3.10, giriş noktası `evren = src.cli:main`) için Claude Code'un VS Code eklentisine benzer bir eklenti geliştireceksin: kenar çubuğunda sohbet paneli, canlı akan ajan çıktısı, araç çağrısı kartları, dosya değişikliklerinde VS Code diff görünümü ve satır içi onay, oturum geçmişi.

Eklenti LLM'e doğrudan bağlanmaz. Tüm ajan mantığı EVREN CLI'da kalır; eklenti CLI'yı alt süreç olarak başlatır ve stdio üzerinden JSON satırlarıyla (NDJSON) konuşur.

## Mevcut durum (kendin doğrula, varsayma)

- `evren -p "<istek>" --output json` başsız kipte tek görevi çalıştırır, en sonda **tek** JSON basar: `result, changed_files, tokens, exit_code, model` (`src/extras.py: run_headless`). Ara olay akışı yok.
- Onaylar `src/ui.py: prompt_confirm` içinden geçer. Başsız kipte soru sorulmaz, varsayılan değer kullanılır. `--yes` ve `--allowed-tools` var.
- Ajan döngüsü: `src/agent.py: AgentSession.run_step(...)`. Araçlar `src/tool_registry.py` / `src/tools.py` içinde: `view_file, edit_file, write_file, create_file, replace_in_file, apply_patch, run_command, list_directory, search_code, grep, ask_user, todo_write, web_fetch, ssh_*`.
- Kipler `src/modes.py` (normal / plan / ask önekleri). Oturumlar `src/sessions.py` (`save_session`, `list_sessions`, `load_session`). `--continue` var.
- Reasoning akışı callback ile terminale basılıyor (`agent.py` ~2314).
- Kurallar: `AGENTS.md` (cerrahi değişiklik, Windows uyumu, doğrulama). Sürüm `pyproject.toml` + `CHANGELOG.md`.

## Faz 1: CLI tarafı, akış protokolü (`evren-cli`)

Mevcut davranışı bozmadan yeni bir kip ekle:

```
evren --stdio [--workspace <yol>] [--model <ad>]
```

- Uzun ömürlü süreç: stdin'den NDJSON komut okur, stdout'a NDJSON olay yazar. Diğer bütün çıktılar (rich, print) **stderr'e** yönlendirilir; stdout yalnız protokol içindir.
- Her mesajda `{"v":1,"type":...,"id":...}` bulunur.

**Gelen komutlar (eklenti → CLI):**
`prompt {text, mode?, attachments?:[{path, range?}]}` · `cancel` · `approval_response {request_id, decision: "allow"|"deny"|"always", edited_content?}` · `ask_user_response {request_id, answer}` · `set_model {model}` · `set_mode {mode}` · `list_sessions` · `resume {session_id}` · `new_session` · `list_models` · `quota` · `slash {command, args}`

**Giden olaylar (CLI → eklenti):**
`ready {version, model, workspace}` · `turn_start` · `assistant_delta {text}` · `reasoning_delta {text}` · `tool_call {call_id, name, args}` · `tool_result {call_id, ok, summary, output_truncated}` · `file_change_proposed {request_id, path, old_content, new_content}` · `approval_request {request_id, kind: "edit"|"command"|"ssh"|"other", title, detail}` · `ask_user {request_id, question, options?}` · `todos {items}` · `usage {prompt_tokens, completion_tokens, quota_left?}` · `turn_end {result, changed_files, exit_code}` · `error {message, code}`

**Uygulama notları:**
- `prompt_confirm` ve `ask_user` için **tek bir kanca noktası** kur (ör. `src/ui.py` içinde değiştirilebilir bir `approval_handler`). stdio kipinde bu kanca `approval_request` yayar ve ilgili `approval_response` gelene kadar bekler. Terminal ve başsız kip aynen kalır.
- Ajan döngüsü ayrı bir thread'de çalışır, stdin okuyucu ana thread'de durur. `cancel` için `src/interrupt.py`'deki mevcut mekanizmayı kullan.
- Dosya düzenleyen araçlar, yazmadan önce `file_change_proposed` olayını önerilen içerikle birlikte yayar. Onay gelince yazar. `edited_content` geldiyse kullanıcının düzelttiği içeriği yazar.
- Uzun araç çıktılarını kırp (`tool_result.summary` ≤ 4 KB). Sırları `sessions._mask` mantığıyla maskele.
- Protokolü `docs/stdio-protocol.md` dosyasında belgele ve şema sürümünü (`v`) koy.
- Testler: `tests/test_stdio_protocol.py`. Sahte istemciyle prompt → delta → tool_call → approval → turn_end akışı, cancel ve geçersiz JSON durumu. Mevcut testlerin tamamı geçmeli.
- Sürümü artır (3.10.0), `CHANGELOG.md`'ye ekle.

## Faz 2: VS Code eklentisi (`evren-vscode`)

**Yığın:** TypeScript, `esbuild`, Webview UI için React + Vite (ya da hafif tutmak için saf TS). `@vscode/test-electron` ile testler. Hedef VS Code ≥1.90.

**Yapı:**
```
src/extension.ts          // activate: komutlar, görünüm, süreç yöneticisi
src/cli/EvrenProcess.ts   // spawn, NDJSON okuma/yazma, yeniden başlatma, zaman aşımı
src/cli/protocol.ts       // Faz 1 tiplerinin TS karşılığı
src/chat/ChatViewProvider.ts  // WebviewViewProvider
src/diff/DiffManager.ts   // önerilen değişiklik için sanal belge + vscode.diff
src/context/Mentions.ts   // @dosya, seçili kod, aktif editör bağlamı
webview/                  // sohbet UI
```

**Özellikler (öncelik sırasıyla):**
1. Activity bar ikonu + kenar çubuğunda sohbet görünümü. Mesaj kutusu, gönder/durdur, markdown + kod vurgulama, akan yanıt, katlanabilir reasoning.
2. Araç çağrısı kartları: ad, kısa argüman, durum (çalışıyor/tamam/hata), genişletilebilir çıktı.
3. Dosya değişikliği onayı: `file_change_proposed` gelince `vscode.diff` ile sol eski / sağ yeni görünüm açılır. Panelde **Kabul / Reddet / Her zaman izin ver** düğmeleri gösterilir. Kullanıcı sağ tarafı düzenleyip kabul ederse düzenlenmiş içerik gönderilir.
4. Komut onayı: `run_command` / `ssh_*` için komut metni ve çalışma dizini gösterilir, onay alınır.
5. Bağlam: `Ctrl+Alt+K` seçili kodu `@yol#L10-20` olarak ekler. Panelde `@` ile dosya seçici açılır. Aktif dosya isteğe bağlı otomatik eklenir.
6. Kip seçici (normal / plan / ask), model seçici (`list_models`), durum çubuğunda model, kip, token ve kota.
7. Oturumlar: yeni oturum, geçmiş listesi, devam ettir.
8. `ask_user` için panelde soru kartı (seçenek düğmeleri ya da serbest metin).
9. Todo listesi paneli (`todos` olayı).
10. Komut paleti: `Evren: Open Chat`, `Evren: New Session`, `Evren: Add Selection to Chat`, `Evren: Cancel`, `Evren: Show Logs`.

**Ayarlar (`contributes.configuration`):**
`evren.cliPath` (varsayılan `evren`) · `evren.pythonPath` (doluysa `python -m src.cli`) · `evren.model` · `evren.defaultMode` · `evren.autoApprove` (`none|edits|all`) · `evren.allowedTools` · `evren.extraEnv`.
API anahtarı ayarlara yazılmaz. CLI'nın kendi yapılandırması (`~/.evren`, `secret_vault`) kullanılır. Gerekirse `SecretStorage` üzerinden ortam değişkeni olarak geçirilir.

**Sağlamlık:**
- CLI yoksa ya da sürümü eski ise (`ready.version` < 3.10.0) kurulum/güncelleme talimatı veren bir uyarı göster.
- Süreç çökerse "Evren" Output kanalına stderr yazılır, tek tıkla yeniden başlatılır. Bekleyen onaylar iptal edilir.
- Windows öncelikli: yollarda boşluk, `cmd.exe`/PowerShell farkı, `shell: false` ile spawn, UTF-8 (`PYTHONIOENCODING=utf-8`).
- Çoklu çalışma alanı klasörlerinde aktif klasörü seç ya da kullanıcıya sor.
- Webview için CSP, `postMessage` mesajlarının tip doğrulaması. Webview'dan doğrudan dosya erişimi yok.

**Kapsam dışı (şimdilik):** satır içi kod tamamlama (inline completion), Marketplace yayını, uzak (Remote-SSH) senaryosu, çoklu paralel ajan.

## Çalışma şekli

1. Önce Faz 1'in mevcut kodla nasıl bağlanacağını 5 maddelik planla yaz (hangi dosya, hangi fonksiyon). Sonra uygula.
2. Faz 1 testleri geçmeden Faz 2'ye başlama. Faz 2'de önce süreç yöneticisi + protokol + düz metin sohbet (1. madde) uçtan uca çalışsın, sonra diğer maddeler.
3. Her fazın sonunda doğrula. Faz 1 için `pytest -q`. Faz 2 için `npm run compile && npm test`, F5 Extension Development Host'ta gerçek bir istekle (ör. "README'ye bir satır ekle") diff + onay akışı.
4. Commit mesajları Türkçe, kısa. AI imzası ya da `Co-Authored-By` yok. Push için onay iste.

## Bitti ölçütü

- `evren --stdio` belgelenmiş, testli, mevcut kipler bozulmamış.
- Eklenti `.vsix` olarak paketleniyor (`vsce package`). Yerelde kurulunca sohbet, akış, araç kartları, diff onayı, komut onayı, iptal, kip/model seçimi ve oturum devamı çalışıyor.
- `evren-vscode/README.md`: kurulum, ayarlar, protokol sürüm uyumu.
