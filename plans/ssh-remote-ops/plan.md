# Plan: SSH ile Uzak Sunucu İşlemlerini Geliştirme

## 1. Amaç ve Kapsam

EVREN CLI şu anda **yalnızca yerel çalışma alanında** çalışıyor. `src/modes.py` ve
`src/tool_gateway.py` yorumlarında C# sürümündeki `ssh/psql` araçlarının "güvenlik
vaadi" gereği bilinçli olarak kapsam dışı bırakıldığı belirtiliyor. Bu plan, bu
yeteneği **güvenli, onaylı ve denetlenebilir** biçimde geri kazandırmayı hedefler.

Hedef: Ajanın, kullanıcı tanımlı uzak sunucularda (host alias'ları üzerinden)
komut çalıştırması, dosya okuma/yazma ve servis yönetimi yapabilmesi — ancak
**her zaman** mevcut mod geçidi (normal/ask/plan), onay akışı ve yedekleme
felsefesiyle uyumlu kalarak.

### Kapsam Dışı (bilinçli)
- Parola tabanlı interaktif SSH (yalnızca anahtar/agent tabanlı kimlik doğrulama).
- Rastgele host'a bağlanma (yalnızca önceden tanımlı `~/.evren/ssh_hosts.json` alias'ları).
- Uzak tarafta otomatik yedekleme (uzak dosya yazımı varsayılan olarak kapalı).

---

## 2. Mevcut Mimari (Referans)

| Katman | Dosya | Rol |
|---|---|---|
| Araç şeması + yürütücü | `src/tools.py` | `TOOLS_SCHEMA`, `execute_tool_call` |
| Mod geçidi | `src/tool_gateway.py` | `check_tool_allowed`, `is_read_only_command` |
| Modlar | `src/modes.py` | `AgentMode` (normal/ask/plan) |
| Ajan döngüsü | `src/agent.py` | `run_step`, araç çağrılarını yürütür |
| Süreç yönetimi | `src/proc_utils.py` | `run_with_tree_kill` (timeout + ağaç öldürme) |
| Onay/UI | `src/ui.py` | `prompt_confirm`, `print_*` |
| REPL | `src/repl.py` | Slash komutları |
| CLI | `src/cli.py` | Alt komutlar |

**Kritik gözlem:** `run_command` zaten `run_with_tree_kill` ile timeout ve süreç
ağacı öldürme sağlıyor. SSH aracı da aynı altyapıyı kullanmalı; `ssh` bir alt
süreç olarak çalıştırılmalı (paramiko gibi ek ağır bağımlılık yerine sistem
`ssh` istemcisi tercih edilmeli — Windows 10+ OpenSSH ile uyumlu).

---

## 3. Tasarım Kararları

### 3.1 Kimlik Doğrulama: Sistem `ssh` istemcisi
- `subprocess` ile `ssh` çağrılır; anahtar/agent tabanlı auth kullanılır.
- `BatchMode=yes` ile parola sorulmadan hızlı başarısız olunur (takılmayı önler).
- `StrictHostKeyChecking=accept-new` + bilinen host anahtarları `~/.ssh/known_hosts`.
- Ek bağımlılık yok → `requirements.txt` değişmez.

### 3.2 Host Tanımları: `~/.evren/ssh_hosts.json`
Rastgele hedefe bağlanmayı engellemek için yalnızca alias'lı host'lar:
```json
{
  "prod-web": {
    "host": "10.0.0.5",
    "user": "deploy",
    "port": 22,
    "identity_file": "C:\\Users\\msi\\.ssh\\id_ed25519",
    "description": "Üretim web sunucusu",
    "allow_write": false
  }
}
```
- `allow_write=false` ise o host'ta yazma araçları reddedilir (varsayılan güvenli).
- Dosya `src/config.py` içindeki `get_evren_dir()` ile aynı kökte tutulur.

### 3.3 Mod Geçidi Entegrasyonu
- `ssh_run` (komut çalıştırma) → `run_command` gibi davranır:
  - `normal`: serbest (onay ile).
  - `ask`/`plan`: yalnızca salt-okuma komutları (`is_read_only_command` yeniden kullanılır).
- `ssh_read_file` → `READ_ONLY_TOOLS`'a eklenir (her modda izinli).
- `ssh_write_file` → `WRITE_TOOLS`'a eklenir (ask/plan'da reddedilir).
- `ssh_list_hosts` → salt-okuma, her modda izinli.

### 3.4 Onay ve Denetim
- Her `ssh_run`/`ssh_write_file` öncesi `prompt_confirm` ile host + komut gösterilir.
- Tüm uzak işlemler `.evren/logs/ssh_audit.log` dosyasına zaman damgasıyla yazılır
  (host, komut, çıkış kodu, süre). Denetlenebilirlik için.

---

## 4. Uygulama Adımları

### Adım 1 — Host kayıt defteri modülü: `src/ssh_hosts.py` (yeni)
- `load_hosts() -> dict[str, SshHost]`: `~/.evren/ssh_hosts.json` okur.
- `SshHost` dataclass: `alias, host, user, port, identity_file, description, allow_write`.
- `get_host(alias) -> SshHost | None`.
- `add_host(...)`, `remove_host(alias)` (REPL `/ssh add` için).
- JSON yoksa boş sözlük döner; bozuk JSON'da anlaşılır hata.

### Adım 2 — SSH çalıştırıcı: `src/ssh_ops.py` (yeni)
- `build_ssh_command(host, remote_command) -> list[str]`:
  `["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
    "-p", str(port), "-i", identity, f"{user}@{host}", remote_command]`
- `ssh_run(alias, command, timeout) -> (code, out, err, timed_out)`:
  `run_with_tree_kill` ile çalıştırır (mevcut altyapı yeniden kullanılır).
- `ssh_read_file(alias, remote_path) -> str`: `cat` ile okur, çıktıyı kırpar.
- `ssh_write_file(alias, remote_path, content) -> str`: `allow_write` kontrolü +
  stdin üzerinden `cat > path` (shell injection'a karşı içerik stdin'den verilir).
- `ssh_list_hosts() -> str`: alias tablosu (maskeli, kimlik dosyası gizli).
- `_audit_log(...)`: `.evren/logs/ssh_audit.log`'a satır ekler.

### Adım 3 — Araç şeması ve yürütücü: `src/tools.py` (düzenle)
- `TOOLS_SCHEMA`'ya 4 yeni araç ekle:
  - `ssh_list_hosts` (parametresiz)
  - `ssh_run` (`alias`, `command`, `timeout_seconds`)
  - `ssh_read_file` (`alias`, `remote_path`)
  - `ssh_write_file` (`alias`, `remote_path`, `content`)
- `execute_tool_call` içine ilgili `elif` dallarını ekle.
- `ssh_run` çıktısı `_truncate_output` ile kırpılır (mevcut fonksiyon yeniden kullanılır).

### Adım 4 — Mod geçidi: `src/tool_gateway.py` (düzenle)
- `READ_ONLY_TOOLS`'a `ssh_list_hosts`, `ssh_read_file` ekle.
- `WRITE_TOOLS`'a `ssh_write_file` ekle.
- `check_tool_allowed` içinde `ssh_run` için `run_command` ile aynı salt-okuma
  kontrolünü uygula (ask/plan'da `is_read_only_command`).
- Modül başındaki "ssh excluded by design" yorumunu güncelle.

### Adım 5 — Sistem promptu: `src/agent.py` (düzenle)
- `SYSTEM_PROMPT`'a SSH araçlarının varlığını ve güvenlik kurallarını ekle:
  - Yalnızca tanımlı alias'lar kullanılır.
  - Uzak yazma öncesi kullanıcı onayı ve `allow_write` kontrolü.
  - Üretim sunucularında önce salt-okuma ile durum tespiti yap.

### Adım 6 — REPL slash komutları: `src/repl.py` (düzenle)
- `/ssh` → host listesi.
- `/ssh add <alias> <user@host> [port]` → host ekle.
- `/ssh remove <alias>` → host sil.
- `/ssh test <alias>` → bağlantı testi (`ssh_run alias "echo ok"`).
- `HELP_TEXT` ve `show_help()` güncelle.

### Adım 7 — CLI alt komutu: `src/cli.py` (düzenle)
- `evren ssh list` / `evren ssh test <alias>` / `evren ssh run <alias> "<cmd>"`.
- Argüman ayrıştırıcıya `ssh` subparser ekle.

### Adım 8 — Yapılandırma ve dokümantasyon
- `.env.example`'a opsiyonel `EVREN_SSH_TIMEOUT`, `EVREN_SSH_CONFIG` ekle.
- `README.md`: yeni "SSH ile Uzak Sunucu İşlemleri" bölümü + güvenlik notları.
- `CHANGELOG.md`: yeni sürüm notu.

---

## 5. Test Planı

Yeni test dosyası: `tests/test_ssh_ops.py` (mevcut `unittest` konvansiyonu).

- `test_build_ssh_command`: doğru bayraklar, port, identity, user@host.
- `test_load_hosts_missing_file`: dosya yoksa boş sözlük.
- `test_load_hosts_malformed_json`: anlaşılır hata.
- `test_ssh_write_blocked_when_allow_write_false`: yazma reddi.
- `test_ssh_run_read_only_gate`: `check_tool_allowed` ile ask/plan'da salt-okuma.
- `test_ssh_tools_in_gateway`: `ssh_read_file` ask'te izinli, `ssh_write_file` değil.
- `ssh_run` gerçek ağ çağrısı **mock'lanır** (`run_with_tree_kill` patch'lenir).

`tests/test_tool_gateway.py`'ye SSH araçları için ek senaryolar.

Çalıştırma: `python -m pytest tests/ -q` (veya `python -m unittest`).

---

## 6. Güvenlik Kontrol Listesi

- [ ] Yalnızca alias'lı host'lara bağlanma (rastgele hedef yok).
- [ ] `BatchMode=yes` → parola prompt'unda takılma yok.
- [ ] `allow_write=false` host'larda yazma reddi.
- [ ] ask/plan modunda `ssh_run` salt-okuma ile sınırlı.
- [ ] Her uzak işlem öncesi kullanıcı onayı (auto_approve hariç).
- [ ] Tüm uzak işlemler audit log'a yazılır.
- [ ] İçerik stdin üzerinden gönderilir (shell injection önlenir).
- [ ] Kimlik dosyası yolları UI'da maskelenir.

---

## 7. Riskler ve Azaltma

| Risk | Azaltma |
|---|---|
| Windows'ta `ssh` yok | Başlangıçta `ssh -V` kontrolü; yoksa anlaşılır hata + kurulum notu |
| Uzak komut zaman aşımı | `run_with_tree_kill` timeout + süreç ağacı öldürme |
| Host anahtarı değişimi | `accept-new`; değişimde kullanıcıya uyarı |
| Büyük çıktı bağlam taşması | `_truncate_output` (head+tail) |
| Yanlışlıkla üretimde yazma | `allow_write` varsayılan `false` + onay |

---

## 8. Dosya Değişiklik Özeti

| Dosya | İşlem |
|---|---|
| `src/ssh_hosts.py` | **Yeni** |
| `src/ssh_ops.py` | **Yeni** |
| `src/tools.py` | Düzenle (şema + dispatch) |
| `src/tool_gateway.py` | Düzenle (izin setleri) |
| `src/agent.py` | Düzenle (system prompt) |
| `src/repl.py` | Düzenle (`/ssh` komutları) |
| `src/cli.py` | Düzenle (`evren ssh` alt komutu) |
| `.env.example` | Düzenle |
| `README.md` | Düzenle |
| `CHANGELOG.md` | Düzenle |
| `tests/test_ssh_ops.py` | **Yeni** |
| `tests/test_tool_gateway.py` | Düzenle |

---

## 9. Onay Sonrası Geçiş

Bu plan onaylandığında **normal moda** geçilerek Adım 1'den itibaren uygulanır.
Her adımda önce ilgili dosya okunur, cerrahi `edit_file` ile değişiklik yapılır ve
`python -m pytest tests/ -q` ile doğrulanır.
