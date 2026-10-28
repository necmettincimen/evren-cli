# Katkı Rehberi

Katkılarınız memnuniyetle karşılanır. Küçük düzeltmeler için doğrudan PR açabilirsiniz; büyük değişiklikler için önce bir issue açıp yaklaşımı konuşalım.

## Geliştirme ortamı

```bash
python -m venv .venv
.venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
```

## Kontroller

```bash
ruff check .
python -m pytest -q
```

Testler gerçek bir modele bağlanmaz. `tests/test_stdio_compat.py`, `evren --stdio` sürecini
`scripts/fake_llm_server.py` sahte sunucusuyla uçtan uca koşturur ve her satırı
`protocol/stdio-v1.schema.json` şemasına göre doğrular.

## stdio protokolünü değiştirmek

[evren-vscode](https://github.com/necmettincimen/evren-vscode) bu protokole bağlıdır. Komut, olay ya da hata kodu eklerseniz:

1. `src/stdio_server.py` içinde değişikliği yapın.
2. `protocol/stdio-v1.schema.json` ve `docs/stdio-protocol.md` dosyalarını güncelleyin. `test_schema_lists_match_server` bu üçünün eşleştiğini denetler.
3. Geriye uyumsuz bir değişiklik `PROTOCOL_VERSION` değerini artırır. Uyumlu eklemeler v1 içinde kalır.
4. CHANGELOG'a yazın.

## Commit ve PR

- Commit mesajları kısa ve Türkçe olsun.
- Her PR'da CI (ruff + pytest, Ubuntu/Windows, Python 3.10–3.12) yeşil olmalıdır.
- Kullanıcıya görünen her değişiklik `CHANGELOG.md` dosyasının en üstüne yazılır.

## Sürüm çıkarma

1. `pyproject.toml` sürümünü ve `CHANGELOG.md` başlığını güncelleyin.
2. `git tag vX.Y.Z && git push origin vX.Y.Z` komutunu çalıştırın. Release iş akışı wheel/sdist dosyalarını ve şemayı ekleyip sürümü yayınlar.
