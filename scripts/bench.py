"""
EVREN CLI - Canlı Benchmark (bench.py)

Sabit görevleri gerçek EVREN ajanıyla koşturup istek sayısı, prompt/önbellek/çıktı
token'ı, süre, başarı ve tekrar engeli sayısını ölçer. Her fazdan önce ve sonra
koşulup karşılaştırılır (plans/cli-stability-speed/plan.md, Faz 0 / 0.2).

DİKKAT: Gerçek koşu GERÇEK KOTA harcar (tam koşu ~1-2M token, çoğu önbellekli) ve
elle tetiklenir. Betik, --dry-run dışında sizden onay ister (--yes ile atlanır).

Kullanım (proje kökünden):
    python scripts/bench.py --dry-run                     # sahte koşucu, kota harcamaz
    python scripts/bench.py --list                        # görevleri listele
    python scripts/bench.py --tasks version-test,brand-cleanup
    python scripts/bench.py --model qwen3.8-flash-next --executor-model deepseek-v4.1-flash
    python scripts/bench.py --compare bench/results/2026-10-17-1030.json
    python scripts/bench.py --report bench/results/2026-10-17-1030.json --compare eski.json

Ölçümler, ajanın çalışma alanındaki .evren/logs/usage.jsonl (ve varsa trace.jsonl)
dosyalarından okunur; yalnızca bu koşuda eklenen satırlar sayılır (dosya ofseti).
Çıktılar bench/results/ altına yazılır (git'e girmez).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "bench"
TASKS_FILE = BENCH_DIR / "tasks.json"
RESULTS_DIR = BENCH_DIR / "results"

RESULT_VERSION = 1
DEFAULT_TIMEOUT = 900
WORKSPACE_KINDS = ("repo-clone", "cwd")

CHECK_TYPES = (
    "command",
    "diff_nonempty",
    "new_commits",
    "version_bumped",
    "text_count",
    "paths_changed",
    "output_regex",
    "no_step_limit",
)

# Only these settings are ever written to the result file (names + values).
# API keys and tokens are deliberately not on this list.
SETTINGS_ENV_NAMES = (
    "EVREN_DEFAULT_MODEL",
    "EVREN_PLANNER_MODEL",
    "EVREN_EXECUTOR_MODEL",
    "EVREN_AUTO_ROUTE",
    "EVREN_EXECUTOR_THINKING",
    "EVREN_REASONING_EFFORT",
    "EVREN_COMPACT_RATIO",
    "EVREN_CONTEXT_BUDGET",
    "EVREN_MAX_STEPS",
    "EVREN_AUTO_APPROVE",
)

# Printed by the agent when it gives up at the step ceiling (src/agent.py).
STEP_LIMIT_RE = re.compile(r"maksimum\s+adım\s+sınırına", re.IGNORECASE)
# Tool result of the repeat guard (src/agent.py); used only when trace.jsonl is absent.
REPEAT_BLOCK_MARKER = "TEKRAR ENGELLENDİ"

_SECRET_NAME_RE = re.compile(r"(?i)(token|secret|password|passwd|api_?keys?)")

STATUS_LABELS = {
    "passed": "başarılı",
    "failed": "başarısız",
    "timeout": "zaman aşımı",
    "skipped": "atlandı",
    "error": "hata",
    "dry-run": "kuru koşu",
}


# --------------------------------------------------------------------------- #
# Görev tanımları
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    workspace: str
    max_steps: int
    description: str = ""
    requires: Mapping[str, Any] = field(default_factory=dict)
    preconditions: tuple = ()
    success: tuple = ()
    timeout: int | None = None


def _parse_checks(task_id: str, raw: Any, label: str) -> tuple:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"'{task_id}': '{label}' bir liste olmalı.")
    checks = []
    for item in raw:
        if not isinstance(item, dict) or item.get("type") not in CHECK_TYPES:
            raise ValueError(
                f"'{task_id}': '{label}' içinde geçersiz kontrol: {item!r} "
                f"(geçerli türler: {', '.join(CHECK_TYPES)})"
            )
        checks.append(dict(item))
    return tuple(checks)


def parse_tasks(data: Any) -> list[Task]:
    """Validates the decoded tasks.json document and returns Task objects."""
    items = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError("tasks.json: 'tasks' boş olmayan bir liste olmalı.")
    tasks: list[Task] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError(f"tasks.json: görev bir nesne olmalı: {raw!r}")
        task_id = str(raw.get("id", "")).strip()
        if not task_id:
            raise ValueError("tasks.json: 'id' zorunlu.")
        if task_id in seen:
            raise ValueError(f"tasks.json: yinelenen görev kimliği: {task_id}")
        seen.add(task_id)
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt:
            raise ValueError(f"'{task_id}': 'prompt' zorunlu.")
        workspace = raw.get("workspace")
        if workspace not in WORKSPACE_KINDS:
            raise ValueError(
                f"'{task_id}': 'workspace' şunlardan biri olmalı: {', '.join(WORKSPACE_KINDS)}"
            )
        max_steps = raw.get("max_steps", 30)
        if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps < 1:
            raise ValueError(f"'{task_id}': 'max_steps' pozitif tam sayı olmalı.")
        timeout = raw.get("timeout")
        if timeout is not None and (not isinstance(timeout, int) or timeout < 1):
            raise ValueError(f"'{task_id}': 'timeout' pozitif tam sayı olmalı.")
        requires = raw.get("requires") or {}
        if not isinstance(requires, dict):
            raise ValueError(f"'{task_id}': 'requires' bir nesne olmalı.")
        tasks.append(
            Task(
                id=task_id,
                prompt=prompt,
                workspace=workspace,
                max_steps=max_steps,
                description=str(raw.get("description", "")),
                requires=requires,
                preconditions=_parse_checks(task_id, raw.get("preconditions"), "preconditions"),
                success=_parse_checks(task_id, raw.get("success"), "success"),
                timeout=timeout,
            )
        )
    return tasks


def load_tasks(path: Path = TASKS_FILE) -> list[Task]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} okunamadı: {exc}") from exc
    return parse_tasks(data)


def select_tasks(tasks: list[Task], wanted: str | None) -> list[Task]:
    """Filters tasks by a comma-separated id list (keeps the file order)."""
    if not wanted:
        return list(tasks)
    names = [part.strip() for part in wanted.split(",") if part.strip()]
    known = {task.id for task in tasks}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(
            f"Bilinmeyen görev: {', '.join(unknown)}. Geçerli: {', '.join(sorted(known))}"
        )
    return [task for task in tasks if task.id in names]


# --------------------------------------------------------------------------- #
# Yapılandırma, ortam, sır maskeleme
# --------------------------------------------------------------------------- #


@dataclass
class BenchConfig:
    repo_root: Path = ROOT
    cwd: Path = field(default_factory=Path.cwd)
    ref: str = "HEAD"
    timeout: int = DEFAULT_TIMEOUT
    model: str | None = None
    executor_model: str | None = None
    planner_model: str | None = None
    dry_run: bool = False
    keep_workspaces: bool = False
    out_dir: Path = RESULTS_DIR
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    python: str = sys.executable
    # Returns the registered SSH aliases for a workspace; None = read ~/.evren registry.
    ssh_aliases: Callable[[Path], Iterable[str]] | None = None
    stamp: str | None = None


def collect_secret_values(env: Mapping[str, str]) -> list[str]:
    """Values of secret-looking env vars (longest first), for log masking."""
    values: set[str] = set()
    for name, value in env.items():
        if not value or not _SECRET_NAME_RE.search(name):
            continue
        for part in re.split(r"[,;]", value) if name.upper().endswith("KEYS") else [value]:
            part = part.strip()
            if len(part) >= 8:
                values.add(part)
    return sorted(values, key=len, reverse=True)


def mask_text(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def build_env(
    task: Task,
    base_env: Mapping[str, str],
    *,
    executor_model: str | None = None,
    planner_model: str | None = None,
) -> dict[str, str]:
    """Environment for the agent process.

    Auto-approve is forced on only inside disposable clones. For `cwd` tasks the
    bench never touches EVREN_AUTO_APPROVE: the child inherits the maintainer's own
    environment (note: EVREN's built-in default is "true").
    """
    env = dict(base_env)
    env["EVREN_AUTO_DOCS"] = "false"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if task.workspace == "repo-clone":
        env["EVREN_AUTO_APPROVE"] = "true"
    if executor_model:
        env["EVREN_EXECUTOR_MODEL"] = executor_model
    if planner_model:
        env["EVREN_PLANNER_MODEL"] = planner_model
    return env


def build_command(task: Task, workspace: Path, *, model: str | None, python: str) -> list[str]:
    argv = [python, "-m", "src.cli", "--workspace", str(workspace)]
    if model:
        argv += ["--model", model]
    argv += ["agent", task.prompt, "--max-steps", str(task.max_steps)]
    return argv


def gather_settings(env: Mapping[str, str], cfg: BenchConfig) -> dict[str, Any]:
    """Model/behaviour settings recorded in the result (whitelisted names only)."""
    return {
        "cli": {
            "model": cfg.model,
            "executor_model": cfg.executor_model,
            "planner_model": cfg.planner_model,
            "ref": cfg.ref,
            "timeout": cfg.timeout,
        },
        "env": {name: env[name] for name in SETTINGS_ENV_NAMES if env.get(name)},
    }


# --------------------------------------------------------------------------- #
# Git ve çalışma alanı
# --------------------------------------------------------------------------- #


def run_git(args: list[str], cwd: Path, *, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-300:]
        raise RuntimeError(f"git {' '.join(args[:2])} başarısız: {tail}")
    return proc


def describe_repo(repo_root: Path) -> dict[str, Any]:
    """Commit, branch and dirty flag of the checkout being benchmarked."""
    info: dict[str, Any] = {"commit": None, "branch": None, "dirty": None}
    try:
        info["commit"] = run_git(["rev-parse", "HEAD"], repo_root).stdout.strip()
        branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root, check=False).stdout.strip()
        info["branch"] = branch or None
        status = run_git(["status", "--porcelain"], repo_root, check=False).stdout
        info["dirty"] = bool(status.strip())
    except (RuntimeError, OSError, subprocess.SubprocessError):
        pass
    return info


def _force_remove(func: Callable, path: str, _exc: Any) -> None:
    # Git object files are read-only on Windows; make them writable and retry.
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> None:
    """rmtree that also deletes read-only files (git objects on Windows)."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_force_remove)
    else:
        shutil.rmtree(path, onerror=_force_remove)


@dataclass
class Workspace:
    path: Path
    base_commit: str | None = None
    temp_root: Path | None = None

    def cleanup(self) -> None:
        if self.temp_root is not None:
            remove_tree(self.temp_root)


def prepare_workspace(task: Task, cfg: BenchConfig) -> Workspace:
    """`cwd` tasks use cfg.cwd; `repo-clone` tasks get a disposable local clone.

    Dry runs always use an empty temp directory so synthetic logs never land in a
    real workspace.
    """
    if cfg.dry_run:
        root = Path(tempfile.mkdtemp(prefix=f"evren-bench-dry-{task.id}-"))
        return Workspace(path=root, base_commit=None, temp_root=root)
    if task.workspace == "cwd":
        return Workspace(path=Path(cfg.cwd))

    root = Path(tempfile.mkdtemp(prefix=f"evren-bench-{task.id}-"))
    dest = root / "repo"
    try:
        # --no-hardlinks: the temp dir is often on another drive than the repo
        # (hard links fail there on Windows) and a clone must stay independent anyway.
        # `clone -c` stores these in the new repo's own config: a commit by the agent
        # then works even on machines without a global git identity.
        run_git(
            [
                "clone", "--local", "--no-hardlinks", "--quiet",
                "-c", "user.name=EVREN Bench",
                "-c", "user.email=bench@evren.invalid",
                "-c", "commit.gpgsign=false",
                str(cfg.repo_root), str(dest),
            ],
            root,
        )
        # Pin the requested ref on a fresh branch. Branch names of the source only
        # exist as origin/<name> in the clone, so try that form as a fallback.
        picked = run_git(["checkout", "--quiet", "-b", "bench", cfg.ref], dest, check=False)
        if picked.returncode != 0:
            run_git(["checkout", "--quiet", "-b", "bench", f"origin/{cfg.ref}"], dest)
        # `origin` of a local clone is the maintainer's real checkout: drop it so
        # nothing the agent does (even `git push`) can reach it.
        run_git(["remote", "remove", "origin"], dest)
        base = run_git(["rev-parse", "HEAD"], dest).stdout.strip()
    except Exception:
        remove_tree(root)
        raise
    return Workspace(path=dest, base_commit=base, temp_root=root)


# --------------------------------------------------------------------------- #
# Gereksinimler
# --------------------------------------------------------------------------- #


def default_ssh_aliases(workspace: Path) -> set[str]:
    """Aliases from EVREN's own registry (src/ssh_hosts.py: machine-wide + project)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.ssh_hosts import load_hosts

    return set(load_hosts(Path(workspace)))


def check_requirements(task: Task, cfg: BenchConfig, workspace_hint: Path) -> str | None:
    """Returns a Turkish skip reason, or None when every requirement is met."""
    missing_env = [name for name in task.requires.get("env", []) if not cfg.env.get(name)]
    if missing_env:
        return "eksik ortam değişkeni: " + ", ".join(missing_env)
    wanted = list(task.requires.get("ssh_hosts", []))
    if wanted:
        loader = cfg.ssh_aliases or default_ssh_aliases
        try:
            registered = set(loader(workspace_hint))
        except Exception as exc:  # malformed registry, missing deps, ...
            return f"SSH kayıt defteri okunamadı: {exc}"
        missing = [alias for alias in wanted if alias not in registered]
        if missing:
            return "kayıtlı olmayan SSH host: " + ", ".join(missing) + " (/ssh add ile ekleyin)"
    return None


# --------------------------------------------------------------------------- #
# Başarı kontrolleri
# --------------------------------------------------------------------------- #


@dataclass
class CheckContext:
    workspace: Path
    base_commit: str | None
    output: str
    python: str
    env: Mapping[str, str]


def _regex_flags(letters: str) -> int:
    flags = 0
    for letter in (letters or "").lower():
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(letter, 0)
    return flags


def _iter_text_files(workspace: Path, paths: Iterable[str]) -> Iterable[Path]:
    for rel in paths:
        target = workspace / rel
        if target.is_file():
            yield target
        elif target.is_dir():
            for item in sorted(target.rglob("*")):
                if item.is_file() and ".git" not in item.parts and ".evren" not in item.parts:
                    yield item


def count_matches(workspace: Path, pattern: str, paths: Iterable[str], flags: str = "") -> int:
    regex = re.compile(pattern, _regex_flags(flags))
    total = 0
    for path in _iter_text_files(workspace, paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += sum(1 for _ in regex.finditer(text))
    return total


def changed_paths(ctx: CheckContext) -> set[str] | None:
    """Paths differing from the base commit (committed, edited or new). None = no base."""
    if not ctx.base_commit:
        return None
    names: set[str] = set()
    diff = run_git(["diff", "--name-only", ctx.base_commit], ctx.workspace, check=False)
    names.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
    fresh = run_git(["ls-files", "--others", "--exclude-standard"], ctx.workspace, check=False)
    names.update(line.strip() for line in fresh.stdout.splitlines() if line.strip())
    cleaned = {name.replace("\\", "/") for name in names}
    # The agent's own logs/backups are not part of the task result.
    return {name for name in cleaned if not name.startswith(".evren/")}


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r'(?m)^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def expected_bump(version: tuple[int, int, int], part: str) -> tuple[int, int, int]:
    major, minor, patch = version
    if part == "major":
        return (major + 1, 0, 0)
    if part == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def _tail(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text[-limit:]


def run_check(spec: Mapping[str, Any], ctx: CheckContext) -> tuple[bool, str]:
    """Runs one check spec. Returns (ok, short Turkish detail)."""
    kind = spec["type"]

    if kind == "command":
        command = str(spec.get("command", "")).strip()
        if not command:
            return False, "komut boş"
        command = command.replace("{python}", f'"{ctx.python}"').replace("{base}", ctx.base_commit or "")
        env = dict(ctx.env)
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.workspace),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=int(spec.get("timeout", 600)),
            )
        except subprocess.TimeoutExpired:
            return False, "komut zaman aşımına uğradı"
        if proc.returncode == 0:
            return True, "çıkış kodu 0"
        return False, f"çıkış kodu {proc.returncode}: {_tail(proc.stdout + ' ' + proc.stderr)}"

    if kind == "diff_nonempty":
        names = changed_paths(ctx)
        if names is None:
            return False, "temel commit yok"
        return (True, f"{len(names)} dosya değişti") if names else (False, "çalışma alanında değişiklik yok")

    if kind == "paths_changed":
        names = changed_paths(ctx)
        if names is None:
            return False, "temel commit yok"
        prefixes = [str(p).replace("\\", "/") for p in spec.get("any", [])]
        hit = sorted(n for n in names if any(n.startswith(p) for p in prefixes))
        return (True, ", ".join(hit[:3])) if hit else (False, "beklenen yollarda değişiklik yok: " + ", ".join(prefixes))

    if kind == "new_commits":
        if not ctx.base_commit:
            return False, "temel commit yok"
        proc = run_git(["rev-list", "--count", f"{ctx.base_commit}..HEAD"], ctx.workspace, check=False)
        try:
            count = int(proc.stdout.strip())
        except ValueError:
            return False, "commit sayısı okunamadı"
        needed = int(spec.get("min", 1))
        return count >= needed, f"{count} yeni commit (en az {needed})"

    if kind == "version_bumped":
        if not ctx.base_commit:
            return False, "temel commit yok"
        base_text = run_git(["show", f"{ctx.base_commit}:pyproject.toml"], ctx.workspace, check=False).stdout
        try:
            new_text = (ctx.workspace / "pyproject.toml").read_text(encoding="utf-8")
        except OSError:
            return False, "pyproject.toml okunamadı"
        old, new = parse_version(base_text), parse_version(new_text)
        if old is None or new is None:
            return False, "sürüm okunamadı"
        part = str(spec.get("part", "patch"))
        want = expected_bump(old, part)
        fmt = lambda v: ".".join(str(n) for n in v)  # noqa: E731
        return new == want, f"{fmt(old)} -> {fmt(new)} (beklenen {fmt(want)})"

    if kind == "text_count":
        try:
            count = count_matches(
                ctx.workspace, str(spec["pattern"]), spec.get("paths", ["."]), str(spec.get("flags", ""))
            )
        except (KeyError, re.error) as exc:
            return False, f"geçersiz tanım: {exc}"
        low, high = spec.get("min"), spec.get("max")
        ok = (low is None or count >= int(low)) and (high is None or count <= int(high))
        bounds = f"min={low}" if low is not None else ""
        bounds += (" " if bounds and high is not None else "") + (f"max={high}" if high is not None else "")
        return ok, f"{count} eşleşme ({bounds})"

    if kind == "output_regex":
        try:
            found = re.search(str(spec["pattern"]), ctx.output, _regex_flags(str(spec.get("flags", "")))) is not None
        except (KeyError, re.error) as exc:
            return False, f"geçersiz tanım: {exc}"
        expect = bool(spec.get("expect", True))
        label = str(spec.get("label") or spec["pattern"])[:60]
        return found == expect, f"çıktıda {'bulundu' if found else 'bulunamadı'}: {label}"

    if kind == "no_step_limit":
        hit = STEP_LIMIT_RE.search(ctx.output) is not None
        return (not hit), ("ajan adım sınırına ulaştı" if hit else "adım sınırına ulaşılmadı")

    return False, f"bilinmeyen kontrol: {kind}"


def evaluate_checks(specs: Iterable[Mapping[str, Any]], ctx: CheckContext) -> list[dict[str, Any]]:
    results = []
    for spec in specs:
        try:
            ok, detail = run_check(spec, ctx)
        except Exception as exc:  # a broken check must not abort the whole run
            ok, detail = False, f"kontrol hatası: {exc}"
        results.append({"type": spec["type"], "ok": bool(ok), "detail": detail})
    return results


# --------------------------------------------------------------------------- #
# Günlük (usage.jsonl / trace.jsonl) okuma ve toplama
# --------------------------------------------------------------------------- #


def log_paths(workspace: Path) -> tuple[Path, Path]:
    logs = Path(workspace) / ".evren" / "logs"
    return logs / "usage.jsonl", logs / "trace.jsonl"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def snapshot_offsets(workspace: Path) -> dict[str, int]:
    """Byte sizes of both logs before a run, so only new lines are counted afterwards."""
    usage, trace = log_paths(workspace)
    return {"usage": _file_size(usage), "trace": _file_size(trace)}


def read_new_records(path: Path, offset: int) -> tuple[list[dict[str, Any]], bool]:
    """JSON records appended after `offset`. Second value: the log was rotated.

    A file smaller than the offset means usage_log rotated it mid-run; everything in
    the new file belongs to this run (lines moved to the archive are not recovered).
    """
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return [], False
    rotated = len(raw) < offset
    chunk = raw if rotated else raw[offset:]
    records: list[dict[str, Any]] = []
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records, rotated


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def aggregate_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    prompts = [v for v in (_int(r.get("prompt_tokens")) for r in records) if v is not None]
    cached = sum(v for v in (_int(r.get("cached_tokens")) for r in records) if v is not None)
    completion = sum(v for v in (_int(r.get("completion_tokens")) for r in records) if v is not None)
    total = 0
    for record in records:
        value = _int(record.get("total_tokens"))
        if value is None:
            value = (_int(record.get("prompt_tokens")) or 0) + (_int(record.get("completion_tokens")) or 0)
        total += value
    prompt_sum = sum(prompts)
    ordered = sorted(prompts)
    steps = [v for v in (_int(r.get("step")) for r in records) if v is not None]
    return {
        "requests": len(records),
        "prompt_tokens": prompt_sum,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_pct": round(100.0 * cached / prompt_sum, 1) if prompt_sum else 0.0,
        "prompt_median": int(round(statistics.median(ordered))) if ordered else 0,
        "prompt_p90": ordered[int(0.9 * (len(ordered) - 1))] if ordered else 0,
        "prompt_max": ordered[-1] if ordered else 0,
        "max_step": max(steps) if steps else 0,
        "models": sorted({str(r["model"]) for r in records if r.get("model")}),
    }


def _scalars(record: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, str):
            out[key] = value[:200]
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
    return out


def aggregate_trace(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarises trace.jsonl records; tolerant of fields that may not exist yet."""
    tools: dict[str, int] = {}
    blocked = tool_count = request_count = 0
    task_end: dict[str, Any] | None = None
    for record in records:
        kind = record.get("type")
        if kind == "request":
            request_count += 1
        elif kind == "tool":
            tool_count += 1
            name = str(record.get("tool") or record.get("name") or "?")
            tools[name] = tools.get(name, 0) + 1
            if record.get("blocked") is True:
                blocked += 1
        elif kind == "task_end":
            task_end = _scalars(record)
    return {
        "records": len(records),
        "requests": request_count,
        "tool_calls": tool_count,
        "blocked": blocked,
        "tools": dict(sorted(tools.items(), key=lambda item: (-item[1], item[0]))),
        "task_end": task_end,
    }


def collect_metrics(workspace: Path, offsets: Mapping[str, int], stdout: str) -> dict[str, Any]:
    usage_path, trace_path = log_paths(workspace)
    usage_records, usage_rotated = read_new_records(usage_path, offsets.get("usage", 0))
    trace_records, trace_rotated = read_new_records(trace_path, offsets.get("trace", 0))

    metrics = aggregate_usage(usage_records)
    trace = aggregate_trace(trace_records) if trace_records else None
    if metrics["requests"] == 0 and trace and trace["requests"]:
        metrics["requests"] = trace["requests"]  # usage log missing: fall back to trace

    if trace is not None:
        blocked, source = trace["blocked"], "trace"
    else:
        blocked, source = stdout.count(REPEAT_BLOCK_MARKER), "stdout"
    return {
        "metrics": metrics,
        "trace": trace,
        "blocked_repeats": blocked,
        "blocked_source": source,
        "step_limit_hit": STEP_LIMIT_RE.search(stdout) is not None,
        "log_rotated": usage_rotated or trace_rotated,
    }


# --------------------------------------------------------------------------- #
# Koşucular
# --------------------------------------------------------------------------- #


@dataclass
class RunRequest:
    task: Task
    workspace: Path
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    timeout: int


@dataclass
class RunResult:
    returncode: int | None
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False


class SubprocessRunner:
    """Runs the real EVREN CLI as a child process. Spends real quota."""

    def __call__(self, request: RunRequest) -> RunResult:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from src.proc_utils import kill_process_tree

        kwargs: dict[str, Any] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True  # own process group for the tree kill
        started = time.monotonic()
        proc = subprocess.Popen(
            request.argv,
            cwd=str(request.cwd),
            env=request.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=request.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = "", ""
        return RunResult(
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            duration=time.monotonic() - started,
            timed_out=timed_out,
        )


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_synthetic_logs(
    workspace: Path,
    usage_rows: Iterable[Mapping[str, Any]] = (),
    trace_rows: Iterable[Mapping[str, Any]] = (),
) -> None:
    """Appends rows to the workspace logs (used by the dry-run runner and tests)."""
    usage_path, trace_path = log_paths(workspace)
    usage_rows, trace_rows = list(usage_rows), list(trace_rows)
    if usage_rows:
        _append_jsonl(usage_path, usage_rows)
    if trace_rows:
        _append_jsonl(trace_path, trace_rows)


class DryRunRunner:
    """Fake runner: invents plausible logs deterministically; the API is never called."""

    def __call__(self, request: RunRequest) -> RunResult:
        rng = random.Random(request.task.id)
        steps = rng.randint(6, 14)
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        usage_rows, trace_rows = [], []
        tools = ("view_file", "find_files", "run_command", "edit_file")
        for step in range(1, steps + 1):
            prompt = 18_000 + step * 1_500 + rng.randint(0, 800)
            usage_rows.append({
                "ts": stamp, "model": "dry-run-model", "prompt_tokens": prompt,
                "completion_tokens": rng.randint(60, 400), "total_tokens": None,
                "cached_tokens": int(prompt * 0.65), "step": step, "mode": "normal",
            })
            trace_rows.append({"type": "request", "step": step, "ts": stamp})
            trace_rows.append({
                "type": "tool", "step": step, "tool": tools[step % len(tools)],
                "blocked": step % 9 == 0,
            })
        trace_rows.append({"type": "task_end", "status": "done", "steps": steps})
        write_synthetic_logs(request.workspace, usage_rows, trace_rows)
        return RunResult(
            returncode=0,
            stdout=f"[kuru koşu] '{request.task.id}' için EVREN çağrılmadı.\n",
            stderr="",
            duration=round(0.5 + rng.random() * 3, 2),
        )


# --------------------------------------------------------------------------- #
# Koşu akışı
# --------------------------------------------------------------------------- #


def unique_stamp(out_dir: Path, stamp: str) -> str:
    candidate, n = stamp, 1
    while (out_dir / f"{candidate}.json").exists() or (out_dir / candidate).exists():
        n += 1
        candidate = f"{stamp}-{n}"
    return candidate


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


def write_task_log(path: Path, request: RunRequest, run: RunResult, secrets: list[str]) -> None:
    header = [
        f"# görev: {request.task.id}",
        f"# çalışma alanı: {request.workspace}",
        f"# komut: {' '.join(request.argv)}",
        f"# çıkış kodu: {run.returncode}  süre: {run.duration:.1f} sn  zaman aşımı: {run.timed_out}",
        "",
        "----- stdout -----",
        run.stdout,
        "",
        "----- stderr -----",
        run.stderr,
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(mask_text("\n".join(header), secrets), encoding="utf-8", newline="\n")


def empty_metrics() -> dict[str, Any]:
    return aggregate_usage([])


def _skipped(task: Task, reason: str) -> dict[str, Any]:
    return {
        "id": task.id, "status": "skipped", "success": None, "skip_reason": reason,
        "duration_s": 0.0, "metrics": empty_metrics(), "trace": None,
        "blocked_repeats": 0, "checks": [], "log": None,
    }


def run_task(task: Task, cfg: BenchConfig, runner: Callable[[RunRequest], RunResult],
             log_dir: Path, secrets: list[str]) -> dict[str, Any]:
    workspace = prepare_workspace(task, cfg)
    try:
        ctx = CheckContext(workspace.path, workspace.base_commit, "", cfg.python, cfg.env)
        if not cfg.dry_run and task.preconditions:
            failed = [c for c in evaluate_checks(task.preconditions, ctx) if not c["ok"]]
            if failed:
                return _skipped(task, "önkoşul sağlanmadı: " + "; ".join(c["detail"] for c in failed))

        offsets = snapshot_offsets(workspace.path)
        request = RunRequest(
            task=task,
            workspace=workspace.path,
            argv=build_command(task, workspace.path, model=cfg.model, python=cfg.python),
            env=build_env(task, cfg.env, executor_model=cfg.executor_model, planner_model=cfg.planner_model),
            cwd=Path(cfg.repo_root),
            timeout=task.timeout or cfg.timeout,
        )
        record: dict[str, Any] = {"id": task.id, "skip_reason": None, "error": None}
        try:
            run = runner(request)
        except Exception as exc:  # the runner itself blew up; keep the other tasks going
            record.update(status="error", success=False, error=mask_text(str(exc), secrets),
                          duration_s=0.0, checks=[], log=None)
            record.update(collect_metrics(workspace.path, offsets, ""))
            return record

        log_path = log_dir / f"{task.id}.log"
        write_task_log(log_path, request, run, secrets)
        output = run.stdout + "\n" + run.stderr
        record.update(collect_metrics(workspace.path, offsets, output))
        record.update(duration_s=round(run.duration, 2), returncode=run.returncode,
                      timed_out=run.timed_out, log=_relative(log_path))

        if cfg.dry_run:
            record.update(status="dry-run", success=None, checks=[])
        elif run.timed_out:
            record.update(status="timeout", success=False, checks=[])
        else:
            # The agent may echo the prompt; keep it out of the pattern checks.
            ctx.output = output.replace(task.prompt, " ")
            checks = evaluate_checks(task.success, ctx)
            for check in checks:
                check["detail"] = mask_text(check["detail"], secrets)
            ok = bool(checks) and all(c["ok"] for c in checks)
            record.update(status="passed" if ok else "failed", success=ok, checks=checks)
        if cfg.keep_workspaces and workspace.temp_root is not None:
            record["workspace"] = str(workspace.path)
        return record
    finally:
        if not cfg.keep_workspaces:
            workspace.cleanup()


def summarize(task_records: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [t for t in task_records if t["status"] != "skipped"]
    sums = {key: sum(t["metrics"].get(key, 0) for t in ran)
            for key in ("requests", "prompt_tokens", "cached_tokens", "completion_tokens", "total_tokens")}
    judged = [t for t in ran if t.get("success") is not None]
    passed = sum(1 for t in judged if t["success"])
    return {
        "tasks": len(task_records),
        "ran": len(ran),
        "skipped": len(task_records) - len(ran),
        "passed": passed,
        "failed": len(judged) - passed,
        "success_rate": round(passed / len(judged), 3) if judged else None,
        **sums,
        "cache_pct": round(100.0 * sums["cached_tokens"] / sums["prompt_tokens"], 1) if sums["prompt_tokens"] else 0.0,
        "duration_s": round(sum(t.get("duration_s", 0.0) for t in ran), 2),
        "blocked_repeats": sum(t.get("blocked_repeats", 0) for t in ran),
    }


def run_benchmark(
    tasks: list[Task],
    cfg: BenchConfig,
    runner: Callable[[RunRequest], RunResult],
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Runs the selected tasks and returns the full result document (not yet saved)."""
    out_dir = Path(cfg.out_dir)
    stamp = cfg.stamp or unique_stamp(out_dir, datetime.now().strftime("%Y-%m-%d-%H%M"))
    log_dir = out_dir / stamp
    secrets = collect_secret_values(cfg.env)
    started = datetime.now()
    records: list[dict[str, Any]] = []
    interrupted = False

    try:
        for index, task in enumerate(tasks, 1):
            hint = Path(cfg.cwd) if task.workspace == "cwd" else Path(cfg.repo_root)
            reason = check_requirements(task, cfg, hint)
            if reason:
                records.append(_skipped(task, reason))
                progress(f"[{index}/{len(tasks)}] {task.id}: atlandı ({reason})")
                continue
            progress(f"[{index}/{len(tasks)}] {task.id}: çalışıyor...")
            record = run_task(task, cfg, runner, log_dir, secrets)
            records.append(record)
            label = STATUS_LABELS.get(record["status"], record["status"])
            extra = f" ({record['skip_reason']})" if record["status"] == "skipped" else ""
            progress(
                f"[{index}/{len(tasks)}] {task.id}: {label}{extra} - "
                f"{record['metrics']['requests']} istek, {record.get('duration_s', 0):.0f} sn"
            )
    except KeyboardInterrupt:
        interrupted = True
        progress("Kesildi (Ctrl+C); o ana kadarki sonuçlar kaydedilecek.")

    return {
        "version": RESULT_VERSION,
        "stamp": stamp,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": cfg.dry_run,
        "interrupted": interrupted,
        "git": describe_repo(Path(cfg.repo_root)),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "settings": gather_settings(cfg.env, cfg),
        "tasks": records,
        "totals": summarize(records),
    }


def save_result(result: Mapping[str, Any], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result['stamp']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def load_result(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} okunamadı: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f"{path}: bench sonuç dosyası değil.")
    return data


# --------------------------------------------------------------------------- #
# Karşılaştırma ve çıktı
# --------------------------------------------------------------------------- #

COMPARE_KEYS = ("requests", "prompt_tokens", "cached_tokens", "completion_tokens",
                "prompt_median", "duration_s", "blocked_repeats")


def _value(task: Mapping[str, Any], key: str) -> float | int:
    if key in ("duration_s", "blocked_repeats"):
        return task.get(key) or 0
    return (task.get("metrics") or {}).get(key) or 0


def _delta(old: float | int, new: float | int) -> dict[str, Any]:
    diff = round(new - old, 2)
    pct = round(100.0 * (new - old) / old, 1) if old else None
    return {"old": old, "new": new, "diff": diff, "pct": pct}


def compare_results(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    """Per-task and total deltas (new vs old) for tasks that actually ran in both."""
    old_by_id = {t["id"]: t for t in old.get("tasks", [])}
    rows: list[dict[str, Any]] = []
    for task in new.get("tasks", []):
        before = old_by_id.get(task["id"])
        if before is None:
            rows.append({"id": task["id"], "note": "eski sonuçta yok"})
            continue
        if task.get("status") == "skipped" or before.get("status") == "skipped":
            rows.append({"id": task["id"], "note": "atlandı"})
            continue
        entry: dict[str, Any] = {
            "id": task["id"],
            "success": {"old": before.get("success"), "new": task.get("success")},
        }
        for key in COMPARE_KEYS:
            entry[key] = _delta(_value(before, key), _value(task, key))
        rows.append(entry)

    totals: dict[str, Any] = {}
    old_t, new_t = old.get("totals") or {}, new.get("totals") or {}
    for key in ("requests", "prompt_tokens", "cached_tokens", "completion_tokens", "duration_s", "blocked_repeats"):
        totals[key] = _delta(old_t.get(key) or 0, new_t.get(key) or 0)
    totals["success_rate"] = {"old": old_t.get("success_rate"), "new": new_t.get("success_rate")}
    return {"tasks": rows, "totals": totals}


def format_delta(delta: Mapping[str, Any]) -> str:
    old, new = delta["old"], delta["new"]
    if delta.get("pct") is None:
        return f"{old} -> {new}"
    return f"{old:,} -> {new:,} ({delta['pct']:+.1f}%)" if isinstance(old, int) and isinstance(new, int) \
        else f"{old} -> {new} ({delta['pct']:+.1f}%)"


def _success_text(pair: Mapping[str, Any]) -> str:
    def word(value: Any) -> str:
        return "?" if value is None else ("evet" if value else "hayır")
    return f"{word(pair['old'])} -> {word(pair['new'])}"


COMPARE_HEADERS = ("Görev", "Başarı", "İstek", "Prompt", "Prompt p50", "Süre (sn)", "Engel")


def compare_rows(comparison: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for entry in comparison["tasks"]:
        if "note" in entry:
            rows.append([entry["id"], entry["note"], "", "", "", "", ""])
            continue
        rows.append([
            entry["id"], _success_text(entry["success"]),
            format_delta(entry["requests"]), format_delta(entry["prompt_tokens"]),
            format_delta(entry["prompt_median"]), format_delta(entry["duration_s"]),
            format_delta(entry["blocked_repeats"]),
        ])
    totals = comparison["totals"]
    rate = totals["success_rate"]
    rows.append([
        "TOPLAM",
        f"{rate['old'] if rate['old'] is not None else '?'} -> {rate['new'] if rate['new'] is not None else '?'}",
        format_delta(totals["requests"]), format_delta(totals["prompt_tokens"]), "",
        format_delta(totals["duration_s"]), format_delta(totals["blocked_repeats"]),
    ])
    return rows


RESULT_HEADERS = ("Görev", "Durum", "İstek", "Prompt", "Önbellek %", "Çıktı", "Prompt p50", "Süre (sn)", "Engel")


def result_rows(result: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for task in result["tasks"]:
        m = task["metrics"]
        status = STATUS_LABELS.get(task["status"], task["status"])
        if task["status"] == "skipped":
            rows.append([task["id"], status, "-", "-", "-", "-", "-", "-", "-"])
            continue
        rows.append([
            task["id"], status, f"{m['requests']:,}", f"{m['prompt_tokens']:,}",
            f"{m['cache_pct']:.1f}", f"{m['completion_tokens']:,}", f"{m['prompt_median']:,}",
            f"{task.get('duration_s', 0):.1f}", str(task.get("blocked_repeats", 0)),
        ])
    t = result["totals"]
    rate = f"{t['passed']}/{t['passed'] + t['failed']}" if (t["passed"] + t["failed"]) else "-"
    rows.append([
        "TOPLAM", rate, f"{t['requests']:,}", f"{t['prompt_tokens']:,}", f"{t['cache_pct']:.1f}",
        f"{t['completion_tokens']:,}", "", f"{t['duration_s']:.1f}", str(t["blocked_repeats"]),
    ])
    return rows


def render_plain(headers: Iterable[str], rows: list[list[str]]) -> str:
    headers = list(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    def fmt(cells: Iterable[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()
    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(row) for row in rows]
    return "\n".join(lines)


def print_table(title: str, headers: Iterable[str], rows: list[list[str]]) -> None:
    """Rich table on a terminal when available, otherwise aligned plain text."""
    if sys.stdout.isatty():
        try:
            from rich.console import Console
            from rich.table import Table

            table = Table(title=title)
            for i, header in enumerate(headers):
                table.add_column(header, justify="left" if i < 2 else "right", no_wrap=True, overflow="fold")
            for row in rows:
                table.add_row(*row)
            Console().print(table)
            return
        except Exception:
            pass
    print(f"\n{title}")
    print(render_plain(headers, rows))


def skip_notes(result: Mapping[str, Any]) -> list[str]:
    return [f"  - {t['id']}: {t['skip_reason']}" for t in result["tasks"] if t["status"] == "skipped"]


# --------------------------------------------------------------------------- #
# Komut satırı
# --------------------------------------------------------------------------- #


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="EVREN CLI canlı benchmark: sabit görevlerle istek/token/süre/başarı ölçümü. "
        "Gerçek koşu GERÇEK KOTA harcar.",
    )
    parser.add_argument("--tasks", help="Virgülle ayrılmış görev kimlikleri (varsayılan: hepsi)")
    parser.add_argument("--list", action="store_true", help="Görevleri listele ve çık")
    parser.add_argument("--model", help="Oturum modeli (evren --model)")
    parser.add_argument("--executor-model", help="Uygulayıcı model (EVREN_EXECUTOR_MODEL)")
    parser.add_argument("--planner-model", help="Planlayıcı model (EVREN_PLANNER_MODEL)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Görev başına saniye (varsayılan: 900)")
    parser.add_argument("--ref", default="HEAD", help="Klon görevleri için kaynak sürüm (varsayılan: HEAD)")
    parser.add_argument("--cwd", help="'cwd' görevlerinin çalışma alanı (varsayılan: geçerli dizin)")
    parser.add_argument("--out-dir", help="Sonuç dizini (varsayılan: bench/results)")
    parser.add_argument("--keep-workspaces", action="store_true", help="Geçici klonları silme")
    parser.add_argument("--compare", metavar="ESKI.json", help="Sonuçları bu eski koşuyla karşılaştır")
    parser.add_argument("--report", metavar="SONUC.json", help="Koşmadan, kayıtlı bir sonucu yazdır")
    parser.add_argument("--dry-run", action="store_true", help="Sahte koşucu: API çağrılmaz, kota harcanmaz")
    parser.add_argument("--yes", "-y", action="store_true", help="Kota harcama onayını sorma")
    return parser


def _print_result(result: Mapping[str, Any]) -> None:
    print_table(f"EVREN bench {result['stamp']}" + (" (kuru koşu)" if result.get("dry_run") else ""),
                RESULT_HEADERS, result_rows(result))
    notes = skip_notes(result)
    if notes:
        print("\nAtlanan görevler:")
        print("\n".join(notes))


def _print_comparison(old_path: str, result: Mapping[str, Any]) -> None:
    old = load_result(Path(old_path))
    comparison = compare_results(old, result)
    print_table(f"Karşılaştırma: {old.get('stamp', old_path)} -> {result.get('stamp')}",
                COMPARE_HEADERS, compare_rows(comparison))


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)

    try:
        if args.report:
            result = load_result(Path(args.report))
            _print_result(result)
            if args.compare:
                _print_comparison(args.compare, result)
            return 0

        tasks = load_tasks()
        if args.list:
            for task in tasks:
                needs = ", ".join(task.requires.get("env", []) + task.requires.get("ssh_hosts", [])) or "-"
                print(f"{task.id:<16} {task.workspace:<10} adım<={task.max_steps:<3} gereken: {needs}")
                if task.description:
                    print(f"    {task.description}")
            return 0
        selected = select_tasks(tasks, args.tasks)
    except ValueError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.yes:
        if not sys.stdin.isatty():
            print("bench: gerçek koşu kota harcar; etkileşimsiz ortamda --yes gerekli.", file=sys.stderr)
            return 2
        answer = input(f"{len(selected)} görev GERÇEK EVREN kotası harcayacak. Devam edilsin mi? [e/H] ").strip().lower()
        if answer not in ("e", "evet", "y", "yes"):
            print("Vazgeçildi.")
            return 1

    if not args.dry_run:
        # Loading src.config also reads .env into os.environ, exactly as the agent
        # process will; requirement checks (DRONE_*, ssh aliases) then see the same values.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            import src.config  # noqa: F401
        except Exception:
            pass

    cfg = BenchConfig(
        cwd=Path(args.cwd).resolve() if args.cwd else Path.cwd(),
        ref=args.ref,
        timeout=args.timeout,
        model=args.model,
        executor_model=args.executor_model,
        planner_model=args.planner_model,
        dry_run=args.dry_run,
        keep_workspaces=args.keep_workspaces,
        out_dir=Path(args.out_dir) if args.out_dir else RESULTS_DIR,
        env=dict(os.environ),
    )
    runner: Callable[[RunRequest], RunResult] = DryRunRunner() if args.dry_run else SubprocessRunner()

    result = run_benchmark(selected, cfg, runner)
    path = save_result(result, cfg.out_dir)
    _print_result(result)
    print(f"\nSonuç: {_relative(path)}")
    if args.compare:
        try:
            _print_comparison(args.compare, result)
        except ValueError as exc:
            print(f"bench: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
