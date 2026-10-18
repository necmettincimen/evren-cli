"""Iterative development mode: assess -> plan -> fix -> verify -> record, round after round.

`evren iterate "<goal>"` (or `/iterate <goal>` in the REPL) keeps working towards a goal without a
human in the loop:

1. checks   — project checks run (auto-detected: flutter analyze/test, pytest, npm test, dotnet
              build; or --check commands). Their failures are the hard signal of the round.
2. plan     — a read-only planning turn reads the goal, the previous HANDOFF and the check output
              and returns the next items as JSON (or {"done": true}).
3. execute  — every item runs as its own fresh agent task (clean history, so one item's context
              never bloats the next) and ends with a `SONUÇ:` line.
4. verify   — checks run again; the delta decides whether the round made progress.
5. record   — `.evren/iterations/<slug>/HANDOFF.md` gets the round summary and state.json the
              machine-readable state, so `--resume` (or a new session) continues where it stopped.

Stops when the planner says the goal is met and checks pass, at --rounds, or after two rounds in a
row without progress (nothing completed and checks not better).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROUND_ITEM_LIMIT = 6
CHECK_TIMEOUT = 900
CHECK_TAIL_LINES = 40


@dataclass
class CheckResult:
    command: str
    code: int
    issues: int
    tail: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class Item:
    title: str
    detail: str = ""
    status: str = "bekliyor"   # bekliyor | tamam | kısmi | engel
    note: str = ""
    round: int = 0


@dataclass
class IterationState:
    goal: str
    slug: str
    checks: list[str]
    round: int = 0
    items: list[Item] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    stalled_rounds: int = 0
    done: bool = False
    started_at: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        return json.dumps(data, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "IterationState":
        data = json.loads(text)
        data["items"] = [Item(**item) for item in data.get("items", [])]
        return cls(**data)


def slugify(goal: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    slug = re.sub(r"[^a-z0-9]+", "-", goal.translate(table).lower()).strip("-")
    return slug[:48] or "iterasyon"


def detect_checks(root: Path) -> list[str]:
    """Project checks that say whether the codebase is healthy, cheapest first."""
    checks: list[str] = []
    if (root / "pubspec.yaml").exists():
        if not (root / ".dart_tool" / "package_config.json").exists():
            checks.append("flutter pub get")
        checks += ["flutter analyze --no-fatal-infos", "flutter test"]
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").is_dir() and any(root.glob("tests/test_*.py")):
        checks.append("python -m pytest -q -x")
    package = root / "package.json"
    if package.exists():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, ValueError):
            scripts = {}
        if "lint" in scripts:
            checks.append("npm run lint --silent")
        if "test" in scripts:
            checks.append("npm test --silent")
    if list(root.glob("*.sln")) or list(root.glob("*.slnx")) or list(root.glob("*.csproj")):
        checks.append("dotnet build -nologo -v q")
    return checks


def _count_issues(command: str, output: str) -> int:
    # flutter/dart analyze: count errors and warnings only (infos are style hints).
    graded = re.findall(r"(?m)^\s*(error|warning) [-•] ", output)
    if graded:
        return len(graded)
    found = re.search(r"(\d+) issues? found", output)
    if found:
        return int(found.group(1))
    failed = re.search(r"(\d+) failed", output)
    if failed:
        return int(failed.group(1))
    if re.search(r"Some tests failed|FAILED|error", output, re.I):
        return max(1, len(re.findall(r"(?im)^\s*(error|FAILED)\b", output)))
    return 0


def run_check(command: str, root: Path) -> CheckResult:
    from src.proc_utils import run_with_tree_kill
    from src.tools import _command_argv

    start = time.monotonic()
    try:
        argv, shell = _command_argv(command, False)
        code, out, err, timed_out = run_with_tree_kill(argv, cwd=str(root), timeout=CHECK_TIMEOUT, shell=shell)
    except Exception as e:  # missing tool, multi-line cmd, ...
        return CheckResult(command, 127, 1, f"{type(e).__name__}: {e}", 0.0)
    text = (out or "") + ("\n" + err if err else "")
    if timed_out:
        code = 124
        text += f"\n[zaman aşımı {CHECK_TIMEOUT} sn]"
    tail = "\n".join(text.strip().splitlines()[-CHECK_TAIL_LINES:])
    issues = _count_issues(command, text) if code != 0 or "issues found" in text else 0
    return CheckResult(command, code, issues, tail, round(time.monotonic() - start, 1))


PLAN_PROMPT = """[İTERASYON PLANLAYICI — tur {round}]
HEDEF: {goal}

Önceki turların özeti (HANDOFF):
{handoff}

Açık maddeler (önceki turlardan): {open_items}

Bu turun başındaki kontrol sonuçları:
{checks}

Görevin: kodu ve ilgili dosyaları okuyarak hedefe ulaşmak için SIRADAKİ en değerli işleri seç.
Kurallar: önce kırık kontrolleri (derleme/analiz/test hatası) düzelten maddeler; sonra hedefteki
eksik özellikler, yapısal hatalar. Her madde tek görevde bitirilebilecek boyutta, somut dosya/ekran adıyla.
En fazla {limit} madde. Hiçbir dosyayı değiştirme.
Son yanıtın YALNIZCA şu JSON olsun (başka metin yok):
{{"done": false, "items": [{{"title": "...", "detail": "neyi, hangi dosyada, kabul ölçütü"}}]}}
Hedef tamamen karşılandıysa ve kontroller geçiyorsa: {{"done": true, "items": []}}"""

ITEM_PROMPT = """[İTERASYON — tur {round}, madde {index}/{total}]
GENEL HEDEF: {goal}
BU MADDE: {title}
AYRINTI / KABUL ÖLÇÜTÜ: {detail}

Yalnızca bu maddeyi yap. Var olan kod stilini koru, gerekiyorsa test ekle/güncelle ve ilgili
kontrolü çalıştırıp doğrula ({checks}). Commit atma, push etme.
Son satırın tam olarak şu biçimde olsun: SONUÇ: tamam|kısmi|engel — kısa açıklama"""


class IterationRunner:
    def __init__(self, session, root: Path, goal: str = "", *, checks: list[str] | None = None,
                 max_rounds: int = 10, item_steps: int = 40, resume: bool = False):
        self.session = session
        self.root = Path(root)
        self.max_rounds = max_rounds
        self.item_steps = item_steps
        base = self.root / ".evren" / "iterations"
        if resume:
            state_file = self._latest_state(base) if not goal else base / slugify(goal) / "state.json"
            if not state_file or not state_file.exists():
                raise ValueError("Devam edilecek iterasyon bulunamadı.")
            self.state = IterationState.from_json(state_file.read_text(encoding="utf-8"))
            if checks:
                self.state.checks = checks
        else:
            if not goal.strip():
                raise ValueError("İterasyon hedefi boş olamaz.")
            self.state = IterationState(goal=goal.strip(), slug=slugify(goal), checks=checks or detect_checks(self.root),
                                        started_at=datetime.now().isoformat(timespec="seconds"))
        self.dir = base / self.state.slug
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _latest_state(base: Path) -> Path | None:
        states = sorted(base.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True) if base.exists() else []
        return states[0] if states else None

    # ------------------------------------------------------------------ record
    @property
    def handoff_path(self) -> Path:
        return self.dir / "HANDOFF.md"

    def _save(self) -> None:
        (self.dir / "state.json").write_text(self.state.to_json(), encoding="utf-8")

    def _append_handoff(self, text: str) -> None:
        if not self.handoff_path.exists():
            self.handoff_path.write_text(
                f"# İterasyon: {self.state.goal}\n\nBaşlangıç: {self.state.started_at}\n"
                f"Kontroller: {', '.join(self.state.checks) or '(yok)'}\n\n", encoding="utf-8")
        with self.handoff_path.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n\n")

    def _handoff_excerpt(self, limit: int = 3500) -> str:
        if not self.handoff_path.exists():
            return "(ilk tur)"
        text = self.handoff_path.read_text(encoding="utf-8")
        return text[-limit:]

    # ------------------------------------------------------------------ steps
    def _run_checks(self, label: str) -> list[CheckResult]:
        from src.ui import print_info

        results = []
        for command in self.state.checks:
            print_info(f"[iterasyon] kontrol ({label}): {command}")
            result = run_check(command, self.root)
            print_info(f"[iterasyon]   → çıkış {result.code}, sorun {result.issues}, {result.seconds} sn")
            results.append(result)
        return results

    @staticmethod
    def _checks_text(results: list[CheckResult]) -> str:
        if not results:
            return "(kontrol tanımlı değil)"
        parts = []
        for r in results:
            status = "GEÇTİ" if r.ok else f"BAŞARISIZ (çıkış {r.code}, {r.issues} sorun)"
            parts.append(f"$ {r.command} → {status}" + ("" if r.ok else f"\n{r.tail[-2500:]}"))
        return "\n".join(parts)

    def _plan(self, checks: list[CheckResult]) -> tuple[bool, list[Item]]:
        from src.modes import AgentMode

        open_items = [f"{i.title} ({i.status}: {i.note[:80]})" for i in self.state.items if i.status in ("kısmi", "engel", "bekliyor")]
        prompt = PLAN_PROMPT.format(round=self.state.round, goal=self.state.goal, handoff=self._handoff_excerpt(),
                                    open_items="; ".join(open_items[-10:]) or "(yok)",
                                    checks=self._checks_text(checks), limit=ROUND_ITEM_LIMIT)
        self.session.clear_history()
        previous = self.session.mode
        self.session.set_mode(AgentMode.ASK)
        try:
            answer = self.session.run_step(prompt, max_steps=25, route=False) or ""
        finally:
            self.session.set_mode(previous)
        return self._parse_plan(answer)

    @staticmethod
    def _parse_plan(answer: str) -> tuple[bool, list[Item]]:
        candidates = re.findall(r"\{.*\}", answer, re.S)
        for raw in sorted(candidates, key=len, reverse=True):
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and "items" in data:
                items = [Item(title=str(x.get("title", "")).strip(), detail=str(x.get("detail", "")).strip())
                         for x in data.get("items") or [] if isinstance(x, dict) and x.get("title")]
                return bool(data.get("done")), items[:ROUND_ITEM_LIMIT]
        # Fallback: numbered lines become items.
        items = [Item(title=m.strip()) for m in re.findall(r"(?m)^\s*\d+[.)]\s+(.+)$", answer)]
        return False, items[:ROUND_ITEM_LIMIT]

    def _execute(self, item: Item, index: int, total: int) -> None:
        from src.modes import AgentMode
        from src.ui import print_info

        print_info(f"[iterasyon] tur {self.state.round} madde {index}/{total}: {item.title}")
        self.session.clear_history()
        self.session.set_mode(AgentMode.NORMAL)
        prompt = ITEM_PROMPT.format(round=self.state.round, index=index, total=total, goal=self.state.goal,
                                    title=item.title, detail=item.detail or "-",
                                    checks=", ".join(self.state.checks) or "uygun kontrol")
        try:
            answer = self.session.run_step(prompt, max_steps=self.item_steps, route=False) or ""
        except Exception as e:  # never let one item end the night
            answer = f"SONUÇ: engel — {type(e).__name__}: {e}"
        found = re.search(r"SONUÇ:\s*(tamam|kısmi|kismi|engel)\s*[—–-]?\s*(.*)", answer, re.I)
        if found:
            item.status = found.group(1).lower().replace("kismi", "kısmi")
            item.note = found.group(2).strip()[:300]
        elif answer.startswith(("HATA", "Hata", "İPTAL")):
            item.status, item.note = "engel", answer[:300]
        else:
            item.status, item.note = "kısmi", (answer.strip().splitlines() or [""])[-1][:300]

    # ------------------------------------------------------------------ loop
    def run(self) -> IterationState:
        from src.ui import print_info, print_success, print_warning

        while not self.state.done and self.state.round < self.max_rounds:
            self.state.round += 1
            started = time.monotonic()
            before = self._run_checks("başlangıç")
            done, items = self._plan(before)
            if done and all(r.ok for r in before):
                self.state.done = True
                self._append_handoff(f"## Tur {self.state.round} — HEDEF TAMAM\nPlanlayıcı hedefi karşılanmış saydı; kontroller geçiyor.")
                self._save()
                print_success("[iterasyon] Hedef karşılandı.")
                break
            if not items:
                print_warning("[iterasyon] Planlayıcı madde üretmedi; tur boş geçti.")
            for item in items:
                item.round = self.state.round
            self.state.items.extend(items)
            self._save()
            for index, item in enumerate(items, 1):
                self._execute(item, index, len(items))
                self._save()
            after = self._run_checks("bitiş")
            completed = sum(1 for i in items if i.status == "tamam")
            issues_before = sum(r.issues + (0 if r.ok else 1) for r in before)
            issues_after = sum(r.issues + (0 if r.ok else 1) for r in after)
            progressed = completed > 0 or issues_after < issues_before
            self.state.stalled_rounds = 0 if progressed else self.state.stalled_rounds + 1
            minutes = round((time.monotonic() - started) / 60, 1)
            summary = {
                "round": self.state.round, "minutes": minutes, "completed": completed, "items": len(items),
                "issues_before": issues_before, "issues_after": issues_after,
                "checks_after": {r.command: r.code for r in after},
            }
            self.state.history.append(summary)
            lines = [f"## Tur {self.state.round} — {datetime.now():%H:%M} ({minutes} dk)",
                     f"Kontrol sorunları: {issues_before} → {issues_after}; tamamlanan {completed}/{len(items)}"]
            lines += [f"- [{i.status}] {i.title}" + (f" — {i.note}" if i.note else "") for i in items]
            failing = [r for r in after if not r.ok]
            if failing:
                lines.append("Kalan kontrol hataları:")
                lines += [f"  - `{r.command}` çıkış {r.code}, {r.issues} sorun" for r in failing]
            self._append_handoff("\n".join(lines))
            self._save()
            print_info(f"[iterasyon] tur {self.state.round} bitti: {completed}/{len(items)} tamam, sorun {issues_before}→{issues_after}")
            if self.state.stalled_rounds >= 2:
                print_warning("[iterasyon] İki tur üst üste ilerleme yok; duruyorum. HANDOFF'a bakın.")
                self._append_handoff("**Durdu:** iki tur üst üste ilerleme yok.")
                break
        self._save()
        print_info(f"[iterasyon] Özet: {self.handoff_path}")
        return self.state


def status_text(root: Path) -> str:
    base = Path(root) / ".evren" / "iterations"
    state_file = IterationRunner._latest_state(base)
    if not state_file:
        return "Kayıtlı iterasyon yok."
    state = IterationState.from_json(state_file.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for item in state.items:
        counts[item.status] = counts.get(item.status, 0) + 1
    last = state.history[-1] if state.history else {}
    return (f"İterasyon: {state.goal}\nTur: {state.round} · durum: {'tamam' if state.done else 'sürüyor'} · "
            f"maddeler: {counts or '-'}\nSon tur: {last or '-'}\nHANDOFF: {state_file.parent / 'HANDOFF.md'}")
