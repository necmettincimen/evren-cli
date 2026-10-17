"""Tests for scripts/bench.py. No test here calls the EVREN API: runners are fakes."""

from __future__ import annotations

import atexit
import contextlib
import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("bench_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_module()

HAVE_GIT = shutil.which("git") is not None
SECRET_VALUE = "drone-secret-value-123456"


class TempDirMixin:
    def make_tmp(self) -> Path:
        path = Path(tempfile.mkdtemp(prefix="evren-bench-test-"))
        self.addCleanup(bench.remove_tree, path)
        return path


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@example.com", *args],
        cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return proc.stdout.strip()


def _build_template_repo(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q")
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "README.md").write_text("# Demo\nForeman'dan uyarlandi.\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "ilk")


_TEMPLATE_REPO: Path | None = None


def make_source_repo(dest: Path) -> Path:
    """A tiny git repo shaped like the real project (version, README, tests dir).

    Built once per test run and copied, because `git init` + commit is slow on Windows.
    """
    global _TEMPLATE_REPO
    if _TEMPLATE_REPO is None:
        base = Path(tempfile.mkdtemp(prefix="evren-bench-template-"))
        atexit.register(bench.remove_tree, base)
        _build_template_repo(base / "repo")
        _TEMPLATE_REPO = base / "repo"
    shutil.copytree(_TEMPLATE_REPO, dest)
    return dest


def usage_row(prompt, cached, completion=50, step=1, model="m-1"):
    return {"ts": "2026-10-17T10:00:00Z", "model": model, "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": prompt + completion,
            "cached_tokens": cached, "step": step, "mode": "normal"}


def make_task(task_id="t1", workspace="cwd", success=(), **kwargs):
    return bench.Task(id=task_id, prompt=f"{task_id} promptu", workspace=workspace,
                      max_steps=kwargs.pop("max_steps", 5), success=tuple(success), **kwargs)


class ParseTasksTest(unittest.TestCase):
    def test_shipped_tasks_file_is_valid(self):
        tasks = bench.load_tasks()
        self.assertEqual(
            [t.id for t in tasks],
            ["drone-failed", "k8s-resources", "k8s-advice", "version-test", "skills-feature", "brand-cleanup"],
        )
        by_id = {t.id: t for t in tasks}
        self.assertEqual(by_id["drone-failed"].requires["env"], ["DRONE_SERVER", "DRONE_TOKEN"])
        self.assertEqual(by_id["k8s-resources"].requires["ssh_hosts"], ["koyu3-k8s"])
        self.assertEqual(by_id["k8s-advice"].requires["ssh_hosts"], ["koyu3-k8s"])
        self.assertEqual(
            {t.id for t in tasks if t.workspace == "repo-clone"},
            {"version-test", "skills-feature", "brand-cleanup"},
        )
        for task in tasks:
            self.assertTrue(task.success, task.id)
            self.assertGreaterEqual(task.max_steps, 1)


    def test_validation_errors(self):
        good = {"id": "a", "prompt": "p", "workspace": "cwd", "max_steps": 3}
        bad_documents = [
            {},
            {"tasks": []},
            {"tasks": [dict(good, id="")]},
            {"tasks": [good, dict(good)]},
            {"tasks": [dict(good, prompt=" ")]},
            {"tasks": [dict(good, workspace="nowhere")]},
            {"tasks": [dict(good, max_steps=0)]},
            {"tasks": [dict(good, success=[{"type": "magic"}])]},
            {"tasks": [dict(good, success="no")]},
        ]
        for doc in bad_documents:
            with self.subTest(doc=doc):
                with self.assertRaises(ValueError):
                    bench.parse_tasks(doc)



class EnvAndCommandTest(unittest.TestCase):
    def test_auto_approve_only_forced_inside_clones(self):
        clone = bench.build_env(make_task(workspace="repo-clone"), {"PATH": "x"})
        self.assertEqual(clone["EVREN_AUTO_APPROVE"], "true")
        cwd_env = bench.build_env(make_task(workspace="cwd"), {"PATH": "x"})
        self.assertNotIn("EVREN_AUTO_APPROVE", cwd_env)
        inherited = bench.build_env(make_task(workspace="cwd"), {"EVREN_AUTO_APPROVE": "false"})
        self.assertEqual(inherited["EVREN_AUTO_APPROVE"], "false")



    def test_secret_collection_and_masking(self):
        env = {
            "DRONE_TOKEN": SECRET_VALUE,
            "EVREN_API_KEYS": "firstkey-value-1,secondkey-value-2",
            "EVREN_API_KEY": "firstkey-value-1",
            "SHORT_TOKEN": "abc",
            "EVREN_EXECUTOR_MODEL": "deepseek-v4.1-flash",
        }
        secrets = bench.collect_secret_values(env)
        self.assertEqual(set(secrets), {SECRET_VALUE, "firstkey-value-1", "secondkey-value-2"})
        masked = bench.mask_text(f"a {SECRET_VALUE} b secondkey-value-2 c", secrets)
        self.assertEqual(masked, "a *** b *** c")

    def test_settings_whitelist_excludes_keys(self):
        cfg = bench.BenchConfig(model="m", executor_model="e")
        env = {"EVREN_EXECUTOR_MODEL": "e", "EVREN_API_KEY": SECRET_VALUE, "DRONE_TOKEN": SECRET_VALUE,
               "EVREN_AUTO_ROUTE": "true"}
        settings = bench.gather_settings(env, cfg)
        self.assertEqual(settings["env"], {"EVREN_EXECUTOR_MODEL": "e", "EVREN_AUTO_ROUTE": "true"})
        self.assertEqual(settings["cli"]["model"], "m")
        self.assertNotIn(SECRET_VALUE, json.dumps(settings))


class AggregateTest(TempDirMixin, unittest.TestCase):
    def test_usage_aggregation(self):
        records = [
            usage_row(1000, 600, 40, step=1),
            usage_row(3000, 2000, 60, step=2, model="m-2"),
            usage_row(2000, 1000, 20, step=3),
            {"prompt_tokens": "bad", "cached_tokens": None, "completion_tokens": True},  # ignored fields
        ]
        agg = bench.aggregate_usage(records)
        self.assertEqual(agg["requests"], 4)
        self.assertEqual(agg["prompt_tokens"], 6000)
        self.assertEqual(agg["cached_tokens"], 3600)
        self.assertEqual(agg["completion_tokens"], 120)
        self.assertEqual(agg["total_tokens"], 6120)
        self.assertEqual(agg["cache_pct"], 60.0)
        self.assertEqual(agg["prompt_median"], 2000)
        self.assertEqual(agg["prompt_max"], 3000)
        self.assertEqual(agg["max_step"], 3)
        self.assertEqual(agg["models"], ["m-1", "m-2"])




    def write_lines(self, path: Path, rows, raw_tail=""):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
            handle.write(raw_tail)





    def test_step_limit_detection(self):
        ws = self.make_tmp()
        out = bench.collect_metrics(ws, {}, "HATA: Ajan maksimum\nadım sınırına (30) ulaştı")
        self.assertTrue(out["step_limit_hit"])
        self.assertFalse(bench.collect_metrics(ws, {}, "tamamlandı")["step_limit_hit"])


class RequirementsTest(unittest.TestCase):
    def cfg(self, env=None, aliases=None):
        return bench.BenchConfig(env=env or {}, ssh_aliases=aliases)

    def test_env_requirement(self):
        task = make_task(requires={"env": ["DRONE_SERVER", "DRONE_TOKEN"]})
        reason = bench.check_requirements(task, self.cfg({"DRONE_SERVER": "https://d"}), Path("."))
        self.assertIn("DRONE_TOKEN", reason)
        self.assertNotIn("DRONE_SERVER", reason)
        ok = self.cfg({"DRONE_SERVER": "https://d", "DRONE_TOKEN": "t"})
        self.assertIsNone(bench.check_requirements(task, ok, Path(".")))
        empty = self.cfg({"DRONE_SERVER": "https://d", "DRONE_TOKEN": ""})
        self.assertIn("DRONE_TOKEN", bench.check_requirements(task, empty, Path(".")))





@unittest.skipUnless(HAVE_GIT, "git gerekli")
class WorkspaceAndChecksTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        self.source = make_source_repo(self.make_tmp() / "kaynak")
        self.cfg = bench.BenchConfig(repo_root=self.source, env={}, python=sys.executable)
        self.task = make_task("c1", workspace="repo-clone")

    def clone(self):
        ws = bench.prepare_workspace(self.task, self.cfg)
        self.addCleanup(ws.cleanup)
        return ws

    def ctx(self, ws, output=""):
        return bench.CheckContext(ws.path, ws.base_commit, output, sys.executable, dict(os.environ))

    def check(self, ws, spec, output=""):
        return bench.run_check(spec, self.ctx(ws, output))

    def test_clone_is_isolated_from_the_source_repo(self):
        ws = self.clone()
        self.assertEqual(ws.base_commit, git(self.source, "rev-parse", "HEAD"))
        self.assertEqual(git(ws.path, "remote"), "")  # push cannot reach the real repo
        self.assertEqual(git(ws.path, "branch", "--show-current"), "bench")
        # (the helper above forces its own identity, so ask git directly)
        self.assertEqual(bench.run_git(["config", "user.email"], ws.path).stdout.strip(), "bench@evren.invalid")
        self.assertTrue((ws.path / "README.md").exists())
        self.assertNotEqual(ws.path.resolve(), self.source.resolve())




    def test_new_commits_and_diff_checks(self):
        ws = self.clone()
        self.assertFalse(self.check(ws, {"type": "new_commits"})[0])
        self.assertFalse(self.check(ws, {"type": "diff_nonempty"})[0])
        (ws.path / "yeni.txt").write_text("a\n", encoding="utf-8")
        self.assertTrue(self.check(ws, {"type": "diff_nonempty"})[0])  # untracked counts
        git(ws.path, "add", "-A")
        git(ws.path, "commit", "-q", "-m", "ajan")
        self.assertTrue(self.check(ws, {"type": "new_commits"})[0])
        self.assertFalse(self.check(ws, {"type": "new_commits", "min": 2})[0])
        self.assertTrue(self.check(ws, {"type": "diff_nonempty"})[0])  # committed change vs base





    def test_command_check_and_placeholders(self):
        ws = self.clone()
        self.assertTrue(self.check(ws, {"type": "command", "command": "{python} -c \"pass\""})[0])
        ok, detail = self.check(ws, {"type": "command", "command": "{python} -c \"raise SystemExit(3)\""})
        self.assertFalse(ok)
        self.assertIn("3", detail)
        base_cmd = "git rev-parse --verify {base}"
        self.assertTrue(self.check(ws, {"type": "command", "command": base_cmd})[0])
        self.assertFalse(self.check(ws, {"type": "command", "command": ""})[0])





@unittest.skipUnless(HAVE_GIT, "git gerekli")
class RunBenchmarkTest(TempDirMixin, unittest.TestCase):
    def setUp(self):
        tmp = self.make_tmp()
        self.source = make_source_repo(tmp / "kaynak")
        self.cwd = tmp / "cwd"
        self.cwd.mkdir()
        self.out = tmp / "out"
        self.env = {"DRONE_TOKEN": SECRET_VALUE, "EVREN_EXECUTOR_MODEL": "exec-1",
                    "EVREN_API_KEY": "evren-key-value-0001", "PATH": os.environ.get("PATH", "")}
        self.cfg = bench.BenchConfig(
            repo_root=self.source, cwd=self.cwd, out_dir=self.out, env=self.env,
            python=sys.executable, stamp="2026-01-01-0000",
            ssh_aliases=lambda ws: {"koyu3-k8s"},
        )
        self.requests = []

    def fake_runner(self, request):
        """Stands in for the agent: edits the workspace and writes logs like EVREN would."""
        self.requests.append(request)
        task_id = request.task.id
        ws = request.workspace
        if task_id == "brand":
            (ws / "README.md").write_text("# Demo\nEVREN'den uyarlandi.\n", encoding="utf-8")
            git(ws, "add", "-A")
            git(ws, "commit", "-q", "-m", "marka")
        usage = [usage_row(1000, 500, step=1), usage_row(2000, 1500, step=2)]
        trace = [{"type": "request"}, {"type": "tool", "tool": "view_file", "blocked": True},
                 {"type": "request"}, {"type": "task_end", "status": "done"}]
        bench.write_synthetic_logs(ws, usage, trace)
        return bench.RunResult(0, f"bitti {SECRET_VALUE}\n", "uyari\n", 2.5)

    def tasks(self):
        brand = make_task("brand", "repo-clone", success=[
            {"type": "new_commits"},
            {"type": "text_count", "pattern": "Foreman", "paths": ["README.md"], "max": 0},
        ], preconditions=({"type": "text_count", "pattern": "Foreman", "paths": ["README.md"], "min": 1},))
        weak = make_task("weak", "repo-clone", success=[{"type": "new_commits"}])
        drone = make_task("drone", "cwd", requires={"env": ["DRONE_SERVER"]})
        k8s = make_task("k8s", "cwd", requires={"ssh_hosts": ["koyu3-k8s"]},
                        success=[{"type": "output_regex", "pattern": "bitti"}])
        return [brand, weak, drone, k8s]

    def test_end_to_end_with_fake_runner(self):
        progress = []
        result = bench.run_benchmark(self.tasks(), self.cfg, self.fake_runner, progress.append)
        by_id = {t["id"]: t for t in result["tasks"]}

        self.assertEqual(by_id["brand"]["status"], "passed")
        self.assertTrue(by_id["brand"]["success"])
        self.assertEqual(by_id["weak"]["status"], "failed")  # agent made no commit there
        self.assertFalse(by_id["weak"]["success"])
        self.assertEqual(by_id["drone"]["status"], "skipped")
        self.assertIn("DRONE_SERVER", by_id["drone"]["skip_reason"])
        self.assertEqual(by_id["k8s"]["status"], "passed")

        brand = by_id["brand"]
        self.assertEqual(brand["metrics"]["requests"], 2)
        self.assertEqual(brand["metrics"]["prompt_tokens"], 3000)
        self.assertEqual(brand["metrics"]["cache_pct"], 66.7)
        self.assertEqual(brand["blocked_repeats"], 1)
        self.assertEqual(brand["duration_s"], 2.5)
        self.assertEqual(brand["trace"]["task_end"], {"type": "task_end", "status": "done"})

        totals = result["totals"]
        self.assertEqual((totals["tasks"], totals["ran"], totals["skipped"]), (4, 3, 1))
        self.assertEqual((totals["passed"], totals["failed"]), (2, 1))
        self.assertAlmostEqual(totals["success_rate"], 0.667)
        self.assertEqual(totals["requests"], 6)
        self.assertEqual(totals["prompt_tokens"], 9000)
        self.assertEqual(totals["blocked_repeats"], 3)
        self.assertEqual(totals["duration_s"], 7.5)
        self.assertTrue(any("drone" in line and "atlandı" in line for line in progress))


    def test_precondition_failure_skips_the_task(self):
        (self.source / "README.md").write_text("# Demo\ntemiz\n", encoding="utf-8")
        git(self.source, "add", "-A")
        git(self.source, "commit", "-q", "-m", "temizlendi")
        result = bench.run_benchmark(self.tasks()[:1], self.cfg, self.fake_runner, lambda line: None)
        record = result["tasks"][0]
        self.assertEqual(record["status"], "skipped")
        self.assertIn("önkoşul", record["skip_reason"])
        self.assertEqual(self.requests, [])


    def test_secrets_never_reach_logs_or_results(self):
        result = bench.run_benchmark(self.tasks(), self.cfg, self.fake_runner, lambda line: None)
        path = bench.save_result(result, self.out)
        self.assertEqual(path, self.out / "2026-01-01-0000.json")
        log = (self.out / "2026-01-01-0000" / "brand.log").read_text(encoding="utf-8")
        self.assertIn("***", log)
        self.assertIn("uyari", log)
        self.assertNotIn(SECRET_VALUE, log)
        saved = path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_VALUE, saved)
        self.assertNotIn("evren-key-value-0001", saved)
        data = json.loads(saved)
        self.assertEqual(data["settings"]["env"], {"EVREN_EXECUTOR_MODEL": "exec-1"})
        self.assertEqual(data["git"]["commit"], git(self.source, "rev-parse", "HEAD"))
        self.assertFalse(data["dry_run"])
        self.assertEqual(bench.load_result(path)["stamp"], "2026-01-01-0000")

    def test_timeout_and_runner_crash_are_recorded(self):
        def runner(request):
            if request.task.id == "slow":
                bench.write_synthetic_logs(request.workspace, [usage_row(10, 0)])
                return bench.RunResult(None, "", "", 900.0, timed_out=True)
            if request.task.id == "crash":
                raise RuntimeError(f"patladi {SECRET_VALUE}")
            return bench.RunResult(0, "tamam", "", 1.0)

        slow = make_task("slow", "cwd", success=[{"type": "no_step_limit"}])
        crash = make_task("crash", "cwd", success=[{"type": "no_step_limit"}])
        after = make_task("after", "cwd", success=[])
        result = bench.run_benchmark([slow, crash, after], self.cfg, runner, lambda line: None)
        by_id = {t["id"]: t for t in result["tasks"]}
        self.assertEqual(by_id["slow"]["status"], "timeout")
        self.assertFalse(by_id["slow"]["success"])
        self.assertEqual(by_id["slow"]["metrics"]["requests"], 1)
        self.assertEqual(by_id["crash"]["status"], "error")
        self.assertNotIn(SECRET_VALUE, by_id["crash"]["error"])
        self.assertEqual(by_id["after"]["status"], "failed")  # no checks defined -> not a success
        self.assertEqual(len(result["tasks"]), 3)



    def test_keyboard_interrupt_keeps_finished_tasks(self):
        def runner(request):
            if request.task.id == "second":
                raise KeyboardInterrupt
            return self.fake_runner(request)

        tasks = [make_task("first", "cwd", success=[{"type": "no_step_limit"}]), make_task("second", "cwd")]
        result = bench.run_benchmark(tasks, self.cfg, runner, lambda line: None)
        self.assertTrue(result["interrupted"])
        self.assertEqual([t["id"] for t in result["tasks"]], ["first"])


class DryRunTest(TempDirMixin, unittest.TestCase):
    def make_cfg(self):
        tmp = self.make_tmp()
        self.cwd = tmp / "cwd"
        self.cwd.mkdir()
        return bench.BenchConfig(
            cwd=self.cwd, out_dir=tmp / "out", dry_run=True, stamp="2026-02-02-0202",
            env={"DRONE_SERVER": "https://drone.invalid", "DRONE_TOKEN": SECRET_VALUE},
            ssh_aliases=lambda ws: {"koyu3-k8s"},
        )

    def test_dry_run_covers_all_shipped_tasks_without_touching_cwd(self):
        cfg = self.make_cfg()
        result = bench.run_benchmark(bench.load_tasks(), cfg, bench.DryRunRunner(), lambda line: None)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["tasks"]), 6)
        for task in result["tasks"]:
            self.assertEqual(task["status"], "dry-run", task["id"])
            self.assertIsNone(task["success"])
            self.assertGreater(task["metrics"]["requests"], 0)
            self.assertGreater(task["metrics"]["prompt_tokens"], 0)
            self.assertGreater(task["duration_s"], 0)
            self.assertEqual(task["metrics"]["models"], ["dry-run-model"])
        self.assertEqual(list(self.cwd.iterdir()), [])  # synthetic logs went to temp dirs
        self.assertIsNone(result["totals"]["success_rate"])



    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bench.main(list(argv))
        return code, out.getvalue(), err.getvalue()




    def test_real_run_refuses_without_confirmation(self):
        # Non-interactive stdin and no --yes: must stop before spending any quota.
        code, _, err = self.run_main("--tasks", "version-test", "--out-dir", str(self.make_tmp()))
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)



class CompareTest(unittest.TestCase):
    def result(self, **overrides):
        def task(task_id, requests, prompt, duration, success=True, blocked=0, status="passed"):
            metrics = bench.aggregate_usage([usage_row(prompt // requests, prompt // 2)] * requests) if requests else bench.aggregate_usage([])
            return {"id": task_id, "status": status, "success": success, "duration_s": duration,
                    "blocked_repeats": blocked, "metrics": metrics, "trace": None}

        tasks = [task("a", 10, 100000, 60.0, blocked=4), task("b", 5, 50000, 30.0)]
        tasks.extend(overrides.pop("extra", []))
        records = tasks
        doc = {"version": 1, "stamp": "x", "tasks": records}
        doc["totals"] = bench.summarize(records)
        return doc

    def test_deltas_per_task_and_totals(self):
        old = self.result()
        new = copy.deepcopy(old)
        new["stamp"] = "y"
        new["tasks"][0]["metrics"]["requests"] = 5
        new["tasks"][0]["metrics"]["prompt_tokens"] = 50000
        new["tasks"][0]["blocked_repeats"] = 0
        new["tasks"][0]["duration_s"] = 30.0
        new["tasks"][1]["success"] = False
        new["totals"] = bench.summarize(new["tasks"])

        cmp = bench.compare_results(old, new)
        a = cmp["tasks"][0]
        self.assertEqual(a["requests"], {"old": 10, "new": 5, "diff": -5, "pct": -50.0})
        self.assertEqual(a["prompt_tokens"]["pct"], -50.0)
        self.assertEqual(a["blocked_repeats"]["diff"], -4)
        self.assertEqual(a["duration_s"]["pct"], -50.0)
        self.assertEqual(cmp["tasks"][1]["success"], {"old": True, "new": False})
        self.assertEqual(cmp["totals"]["requests"]["old"], 15)
        self.assertEqual(cmp["totals"]["requests"]["new"], 10)
        self.assertEqual(cmp["totals"]["success_rate"], {"old": 1.0, "new": 0.5})





class SubprocessRunnerTest(TempDirMixin, unittest.TestCase):
    """The real runner, pointed at harmless python one-liners instead of the EVREN CLI."""

    def request(self, code, timeout):
        tmp = self.make_tmp()
        return bench.RunRequest(task=make_task(), workspace=tmp, argv=[sys.executable, "-c", code],
                                env=dict(os.environ), cwd=tmp, timeout=timeout)


    def test_timeout_kills_the_process(self):
        run = bench.SubprocessRunner()(self.request("import time; time.sleep(60)", 1))
        self.assertTrue(run.timed_out)
        self.assertLess(run.duration, 30)


if __name__ == "__main__":
    unittest.main()
