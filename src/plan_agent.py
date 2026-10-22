"""plan `agent` ve `parallel` adımları: sonuç sözleşmesi, soru yakalama, tam çıktı günlüğü, git worktree yalıtımı, canlı ilerleme.

Sözleşme (agent adımı): adım yalnız şu koşullar sağlanınca başarılı sayılır; sağlanmazsa `retries` kez (varsayılan 1)
eksiğe özel uyarıyla yeniden denenir, yine sağlanmazsa HATA verir:
  - `require_changes` (varsayılan true): git deposunda dosya değişmiş olmalı;
  - `commit` (varsayılan false): HEAD ilerlemiş olmalı (worktree kipinde kalan değişiklikleri Evren kendisi commit eder);
  - `report` (yol): ajan bu dosyayı yazmış ve boş olmamalı (salt rapor/keşif adımları için `require_changes: false` ile);
  - ajanın son sözü soru ile bitmemeli ("onay ister misin?" gibi) — soru sorarak biten çıktı eksik sayılır.
Ajanın TÜM çıktısı `reports/agent-logs/<etiket>-a<deneme>.log` dosyasına yazılır; ekranda yalnız son satır + yol görünür.
`push: true`: iş ana ağaca alınınca Evren hemen push eder (Drone sonucunu bekler; pushlar sıralı) — `--allow-push` gerekir.
`worktree: true` (repo != kök): iş `.evren/worktrees/<etiket>` altındaki geçici git worktree'de ve `evren/<etiket>` dalında
yapılır, bitince ana ağaca ff-only/cherry-pick ile alınır; yarım kalan iş ana ağacı kirletmez. `parallel` adımı alt
adımları eşzamanlı koşturur (agent alt adımlarında worktree varsayılan açık).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_PRINT_LOCK = threading.Lock()
_MERGE_LOCK = threading.Lock()
_PROGRESS_EVERY_S = 30
PUSH_HOOK = None  # plan_runner doldurur: callable(step) -> özet (kilitli _op_push)

_HEADLESS_DIRECTIVE = (
    "\n\n[BAŞSIZ KİP] Yalnız bu dar görevi uygula; plan sunma, soru sorma, onay isteme. "
    "Belirsizlikte en makul varsayımla ilerle ve varsayımı raporda yaz. "
    "Analiz ya da rapor yazıp bitirme: bulduğun her yeri HEMEN edit_file/write_file ile uygula. "
    "Bağımsız okuma ve aramaları AYNI turda birden çok araç çağrısıyla yap "
    "(view_file + search_code + find_files birlikte); dosyaları tek tek sırayla okuma. "
    "Bitirmeden önce derlemeyi/çalıştırmayı tek komutla doğrula. "
    "Büyük dosyayı TEK write_file çağrısında yazma (çıktı sınırında kesilir ve iş döngüye girer): "
    "çağrı başına en çok ~2500 karakter; ilk parça write_file, devamı write_file(append=true)."
)
_NOTE_NO_CHANGE = ("\n\n[UYARI] Önceki deneme hiçbir dosyayı değiştirmedi. Bu kez analiz yazmadan, "
                   "ilk turda ilgili dosyaları birlikte okuyup ikinci turda edit_file ile değişiklikleri uygula.")
_NOTE_NO_COMMIT = ("\n\n[UYARI] Önceki deneme değişiklikleri commit etmedi. İşi bitirince yalnız dokunduğun dosyaları "
                   "git add ile ekleyip commit at (git add -A kullanma).")
_NOTE_NO_REPORT = "\n\n[UYARI] Önceki deneme istenen rapor dosyasını yazmadı. İşi bitirmeden önce raporu yaz."
_NOTE_QUESTION = ("\n\n[UYARI] Önceki deneme soru/onay isteyerek bitti. SORU SORMA: kimse cevap vermeyecek. "
                  "Belirsizlikte en makul varsayımı seç, işi sonuna kadar yap ve varsayımı raporda belirt.")

_QUESTION_RE = re.compile(
    r"(devam edeyim mi|yazayım mı|geçeyim mi|başlayayım mı|onay(ın|ınla)? .*\b(mı|mi|mu|mü)\b|hangisiyle|"
    r"should i (proceed|continue)|do you want|would you like)", re.I)


def asks_question(text: str) -> bool:
    """Çıktının son sözü soru/onay isteği mi? (yalnız kuyruk bakılır; gövdedeki soru işaretleri sayılmaz)."""
    tail = text.strip()[-350:]
    if not tail:
        return False
    return tail.rstrip("*_` \n").endswith("?") or bool(_QUESTION_RE.search(tail))


def _say(msg: str) -> None:
    with _PRINT_LOCK:
        print(msg, flush=True)


def _git(repo: Path, *args: str, timeout: int = 120, check: bool = False, inp: bytes | None = None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=inp, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: " + (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-300:])
    return r


def _is_git(path: Path) -> bool:
    return path.is_dir() and _git(path, "rev-parse", "--git-dir").returncode == 0


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def _state(repo: Path) -> str | None:
    """HEAD + çalışma ağacı özeti; git deposu değilse None."""
    try:
        st = _git(repo, "status", "--porcelain", timeout=60)
        if st.returncode != 0:
            return None
        return _head(repo) + "\n" + st.stdout.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "agent"


def _run_once(cmd: list[str], logpath: Path, label: str, timeout: int) -> tuple[int, str]:
    """Ajanı çalıştırır; tüm çıktıyı dosyaya yazar, 30 sn'de bir canlı ilerleme satırı basar."""
    logpath.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    last_tick = t0
    with open(logpath, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        while True:
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            if now - t0 > timeout:
                proc.kill()
                raise RuntimeError(f"ajan {timeout} sn zaman aşımı ({label}); günlük: {logpath}")
            if now - last_tick >= _PROGRESS_EVERY_S:
                last_tick = now
                try:
                    lines = [x for x in logpath.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
                except OSError:
                    lines = []
                _say(f"  … {label} çalışıyor {int(now - t0)} sn | {len(lines)} satır | "
                     f"{(lines[-1] if lines else '')[:100]}")
    text = logpath.read_text(encoding="utf-8", errors="replace")
    return proc.returncode, text


def _merge_back(repo: Path, branch: str, base: str) -> str:
    """worktree dalını ana ağaca alır: önce ff-only, olmazsa cherry-pick; çatışmada iptal edip hata verir."""
    with _MERGE_LOCK:
        if _git(repo, "merge", "--ff-only", branch).returncode == 0:
            return "ff"
        r = _git(repo, "cherry-pick", f"{base}..{branch}")
        if r.returncode == 0:
            return "cherry-pick"
        _git(repo, "cherry-pick", "--abort")
        raise RuntimeError(f"ana ağaca alınamadı ({branch}); dal ve worktree incelenmek üzere bırakıldı: "
                           + (r.stdout + r.stderr).decode("utf-8", "replace").strip()[-250:])


def op_agent(step: dict, root: Path, dry: bool, resolve) -> str:
    if dry:
        return "agent adımı (kuru çalıştırmada atlandı)"
    label = _slug(step.get("_label", "agent"))
    repo = resolve(root, step.get("repo", "."))
    is_git = _is_git(repo)
    if (step.get("worktree") or step.get("commit") or step.get("push")) and not is_git:
        raise ValueError("worktree/commit/push için `repo` (git deposu) belirtilmeli; belirtilmezse sözleşme sessizce devre dışı kalıyordu")
    need_changes = bool(step.get("require_changes", True)) and is_git
    need_commit = bool(step.get("commit", False)) and is_git
    report = step.get("report")
    use_wt = bool(step.get("worktree")) and is_git and repo != root
    attempts = int(step.get("retries", 1)) + 1

    work_root, work_repo, branch, base = root, repo, None, None
    if use_wt:
        base = _head(repo)
        branch = f"evren/{label}"
        wt = root / ".evren" / "worktrees" / label
        if wt.exists():
            _git(repo, "worktree", "remove", "--force", str(wt))
        _git(repo, "branch", "-D", branch)
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(repo, "worktree", "add", "-b", branch, str(wt), base, check=True, timeout=300)
        work_root = work_repo = wt

    prompt = step["prompt"]
    if use_wt:
        for variant in {str(repo), str(repo).replace("\\", "/"), str(repo).replace("/", "\\")}:
            prompt = re.sub(re.escape(variant), lambda m: str(work_repo).replace("\\", "/"), prompt, flags=re.I)
        prompt += (f"\n\n[ÇALIŞMA DİZİNİ] Bu görev geçici bir kopyada koşuyor: repo kökü {str(work_repo).replace(chr(92), '/')}. "
                   "Yalnız bu dizinde çalış; üst dizinlere/diğer repolara dokunma.")
    report_in_agent = None
    if report:
        report_in_agent = ".evren-rapor.md" if use_wt else report
        prompt += (f"\n\n[RAPOR] Sonucu (kök neden, yapılan değişiklik, doğrulama, kalan iş, varsayımlar) "
                   f"{str(work_root / report_in_agent).replace(chr(92), '/')} dosyasına yaz"
                   + (" ve bu dosyayı commit'e KATMA." if use_wt else "."))

    if report and (root / report).is_file():
        (root / report).unlink()  # eski sonuç yeni koşunun sonucu sayılmasın
    before = _state(work_repo) if (need_changes or need_commit) else None
    head_before = _head(work_repo) if is_git else None
    head_before_main = _head(repo) if is_git else None
    notes: list[str] = []
    last = "tamam"
    problems = ""
    try:
        for attempt in range(attempts):
            cmd = [sys.executable, "-m", "src.cli", "-w", str(work_root), "--yes", "--max-steps", str(step.get("max_steps", 80))]
            if step.get("model"):
                cmd += ["-m", step["model"]]
            cmd += ["-p", prompt + _HEADLESS_DIRECTIVE + "".join(notes)]
            logpath = root / "reports" / "agent-logs" / f"{label}-a{attempt + 1}.log"
            rc, text = _run_once(cmd, logpath, label, int(step.get("timeout", 1800)))
            if rc != 0:
                raise RuntimeError(text.strip()[-400:] + f"\n(günlük: {logpath})")
            last = (text.strip().splitlines() or ["tamam"])[-1][:200]
            rel_log = logpath.relative_to(root).as_posix()

            missing: list[str] = []
            if use_wt:
                # Rapor ajan kopyasından ana kökteki yerine taşınır (commit'e girmesin); kalan değişiklikleri Evren commit eder.
                if report_in_agent and (work_repo / report_in_agent).exists():
                    (root / report).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(work_repo / report_in_agent), str(root / report))
                if _git(work_repo, "status", "--porcelain").stdout.strip():
                    _git(work_repo, "add", "-A")
                    _git(work_repo, "commit", "-q", "-m", step.get("commit_message") or f"agent: {label}")
            if need_changes and _state(work_repo) == before and _head(work_repo) == head_before:
                missing.append("değişiklik yok")
                notes = [_NOTE_NO_CHANGE]
            elif need_commit and _head(work_repo) == head_before:
                missing.append("commit yok")
                notes = [_NOTE_NO_COMMIT]
            if report and not ((root / report).is_file() and (root / report).stat().st_size > 0):
                missing.append("rapor yok")
                notes = notes + [_NOTE_NO_REPORT]
            if missing and asks_question(text):
                missing.append("soru sorarak bitti")
                notes = notes + [_NOTE_QUESTION]
            if not missing:
                suffix = f" [günlük: {rel_log}]" + (f" [rapor: {report}]" if report else "")
                merged = ""
                if use_wt:
                    if _head(work_repo) != base:
                        merged = f" [ana ağaca alındı: {_merge_back(repo, branch, base)}]"
                    _git(repo, "worktree", "remove", "--force", str(work_repo))
                    _git(repo, "branch", "-D", branch)
                pushed = ""
                if step.get("push") and is_git and PUSH_HOOK is not None and _head(repo) != head_before_main:
                    pushed = " [push: " + PUSH_HOOK(step) + "]"
                return (f"(deneme {attempt + 1}) " if attempt else "") + last + merged + pushed + suffix
            problems = ", ".join(missing)
            _say(f"  ! {label} deneme {attempt + 1}/{attempts} eksik: {problems}")
        raise RuntimeError(f"ajan sözleşmeyi karşılamadı ({problems}); son çıktı: {last} (günlük: reports/agent-logs/{label}-a*.log)"
                           + (f"; yarım iş dalı: {branch}" if use_wt else ""))
    except Exception:
        raise


def op_parallel(step: dict, root: Path, dry: bool, ops: dict, resolve, expand_fn, ctx: dict) -> str:
    """Alt adımları eşzamanlı koşturur (`max_workers`, varsayılan 3). Hepsi bitince hataları toplayıp bildirir."""
    from concurrent.futures import ThreadPoolExecutor

    subs = step["steps"]
    workers = max(1, int(step.get("max_workers", 3)))
    base_label = step.get("_label", "parallel")

    def run(j_sub):
        j, sub = j_sub
        sub = expand_fn(dict(sub), ctx)
        sub["_label"] = f"{base_label}.{j}"
        if sub.get("op") == "agent":
            sub.setdefault("worktree", True)
        fn = ops.get(sub.get("op"))
        if fn is None:
            raise ValueError(f"parallel içinde desteklenmeyen adım: {sub.get('op')}")
        if sub.get("op") == "agent":
            return fn(sub, root, dry)
        return fn(sub, root, dry)

    results: list[str] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [(j, pool.submit(run, (j, s))) for j, s in enumerate(subs, 1)]
        for j, f in futs:
            try:
                out = f.result()
                results.append(f"{j}: {out}")
                _say(f"  ✓ alt adım {base_label}.{j}: {out}")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{j}: {e}")
                _say(f"  ✗ alt adım {base_label}.{j}: {e}")
    if errors:
        raise RuntimeError(f"{len(errors)}/{len(subs)} alt adım başarısız:\n" + "\n".join(errors))
    return f"{len(subs)} alt adım tamam"
