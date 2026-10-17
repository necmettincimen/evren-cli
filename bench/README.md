# EVREN canlı benchmark

`scripts/bench.py`, 6 sabit görevi **gerçek EVREN ajanıyla** koşturup her görev için istek sayısı,
prompt / önbellek / çıktı token'ı, süre, başarı ve tekrar engeli sayısını ölçer. Amaç: her fazdan önce
ve sonra aynı ölçümü alıp farkı görmek (`plans/cli-stability-speed/plan.md`, Faz 0 / 0.2).

> **Gerçek kota harcar.** Tam koşu tahminen **1-2M token** tüketir (büyük kısmı önbellekli). Elle
> tetiklenir; CI'da çalışmaz. Kota harcamadan denemek için `--dry-run` kullanın.

## Çalıştırma

```powershell
python scripts/bench.py --dry-run                 # sahte koşucu: API yok, kota yok
python scripts/bench.py --list                    # görevler ve gereksinimleri
python scripts/bench.py                           # tüm görevler (onay sorar)
python scripts/bench.py --tasks version-test,brand-cleanup --yes
python scripts/bench.py --model qwen3.8-flash-next --executor-model deepseek-v4.1-flash
python scripts/bench.py --compare bench/results/2026-10-17-1030.json     # koşu sonrası farklar
python scripts/bench.py --report bench/results/yeni.json --compare bench/results/eski.json
```

Seçenekler: `--tasks a,b`, `--model`, `--executor-model` (`EVREN_EXECUTOR_MODEL`), `--planner-model`
(`EVREN_PLANNER_MODEL`), `--timeout` (görev başına sn, varsayılan 900), `--ref` (klon görevleri için
kaynak sürüm, varsayılan `HEAD`), `--cwd` (`cwd` görevlerinin çalışma alanı), `--keep-workspaces`,
`--out-dir`, `--yes`, `--dry-run`.

## Görevler (`bench/tasks.json`)

| Kimlik | Çalışma alanı | Gereksinim | Başarı |
|---|---|---|---|
| `drone-failed` | `cwd` | env `DRONE_SERVER`, `DRONE_TOKEN` | çıktıda hatalı build durumu, adım tavanı aşılmadı |
| `k8s-resources` | `cwd` | SSH host `koyu3-k8s` kayıtlı | çıktıda request + limit + cpu/memory |
| `k8s-advice` | `cwd` | SSH host `koyu3-k8s` kayıtlı | çıktıda öneri + pod durumu |
| `version-test` | `repo-clone` | - | yeni commit, yama sürümü +1, testler geçer |
| `skills-feature` | `repo-clone` | - | fark var, `skill_counts` eklendi, `tests/` değişti, testler geçer |
| `brand-cleanup` | `repo-clone` | README'de "Foreman" var | README'de 0 eşleşme, README değişti, testler geçer |

Gereksinimi karşılanmayan görev **atlanır** (nedeni sonuçta yazılır). İlk üç görevin başarı kontrolü
çıktı üzerinde sezgiseldir; asıl kanıt `bench/results/<koşu>/<görev>.log` dosyasıdır.

Başarı kontrolü türleri (`success` / `preconditions`): `command` (kabuk komutu, çıkış kodu 0;
`{python}` ve `{base}` yer tutucuları), `new_commits`, `version_bumped`, `diff_nonempty`,
`paths_changed`, `text_count` (min/max), `output_regex`, `no_step_limit`. Hepsi sağlanırsa görev başarılıdır.

## Güvenlik

- `repo-clone` görevleri `git clone --local` ile geçici klonda koşar (bitince silinir); klonda `origin`
  kaldırılır, yani `git push` gerçek deponuza ulaşamaz. Yalnızca bu görevlerde `EVREN_AUTO_APPROVE=true`
  verilir. Klon, `--ref` (varsayılan `HEAD`) sürümünün **commit edilmiş** halidir; çalışma ağacındaki
  commit edilmemiş değişiklikler klona girmez, ama test edilen CLI kodu bulunduğunuz çalışma ağacıdır.
- `cwd` görevlerinde bench `EVREN_AUTO_APPROVE`'a **dokunmaz**; ajan sizin ortamınızı devralır
  (EVREN varsayılanı `true`). Onaysız denemek için `EVREN_AUTO_APPROVE=false` verin: `run_command` /
  `ssh_run` onayı stdin kapalı olduğundan reddedilir ve bu görevler başarısız olur.
- Drone belirteci yalnızca ortam değişkeniyle ajana geçer; hiçbir dosyaya yazılmaz. Günlüklerde ve
  sonuçta belirteç/anahtar değerleri `***` ile maskelenir. Sonuç dosyasına yalnızca model ayarları
  (`EVREN_*MODEL`, `EVREN_AUTO_ROUTE` vb.) yazılır, anahtar yazılmaz.
- Ajan her görevde `EVREN_AUTO_DOCS=false` ile açılır (bağlam dosyaları üretilmez).

## Ölçümün kaynağı

Her görevden sonra ajanın çalışma alanındaki `.evren/logs/usage.jsonl` okunur (istek başına
`prompt_tokens`, `cached_tokens`, `completion_tokens`, `model`, `step`); varsa `trace.jsonl`
(`request` / `tool` / `task_end` kayıtları, araç kaydındaki `blocked`) tekrar engeli sayısı için
kullanılır. Yalnızca o koşuda eklenen satırlar sayılır (dosya ofseti). `trace.jsonl` yoksa tekrar engeli,
çıktıdaki `TEKRAR ENGELLENDİ` ibaresinden sayılır (`blocked_source: stdout`).

`cwd` görevlerinde aynı çalışma alanında başka bir EVREN oturumu açıkken koşmayın; günlüğe onun
satırları da karışır. Temiz bir dizinde koşmak için `--cwd <boş dizin>` kullanın (SSH kayıt defteri
makine geneli `~/.evren/ssh_hosts.json` olduğundan etkilenmez).

## Çıktılar

- `bench/results/<YYYY-AA-GG-SSDD>.json`: görev başına metrikler, toplamlar, git commit, model ayarları.
- `bench/results/<YYYY-AA-GG-SSDD>/<görev>.log`: ajanın stdout/stderr çıktısı.

`bench/results/` git'e girmez. Bir taban çizgisini depoda saklamak isterseniz `git add -f` kullanın.
