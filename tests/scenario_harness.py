"""End-to-end scenario (replay) harness for EVREN CLI.

Unit tests verify pieces one by one; the regressions that slipped through
(REPL NameError at startup, a dropped docs bootstrap call, background commands
skipping approval, PATH lost while a secret exists, ...) lived in the seams
between those pieces. A scenario replays one whole interaction instead:

    scripted user input -> real AgentSession / REPL -> scripted model replies
    -> REAL tool dispatcher in a temp workspace -> assertions on what happened

Only three things are fake: the model (a MagicMock client whose
`chat_stream_assembled` returns the scripted replies), the terminal (every
`input()` is answered from the scenario) and, optionally, individual tool
results (`tool_results`). A new scenario is a new JSON file in
`tests/scenarios/`; `tests/test_scenarios.py` picks it up automatically.

Scenario file schema (unknown keys are rejected so typos cannot pass silently)::

    name            str   required, must equal the file name without ".json"
    description     str   required; say which bug the scenario guards against
    kind            "agent" (default) | "repl"
                    agent: each user input is one `AgentSession.run_step(text)`.
                    repl:  `src.repl.start_repl` runs on a non-TTY; each user
                           input is one line typed at a prompt (use "/mode ask"
                           lines to change the mode there).
    mode            "normal" (default) | "ask" | "plan" (agent kind only)
    env             {str: str} environment overrides. Every EVREN_* variable of
                    the developer's shell/.env is removed first; defaults are
                    EVREN_AUTO_APPROVE=false, EVREN_AUTO_DOCS=false,
                    EVREN_AUTO_ROUTE=false. EVREN_AUTO_APPROVE is also patched
                    into the modules that read it at import time. HOME and
                    EVREN_CLI_HOME point into the temp directory.
    workspace_files {relative path: text} created in the temp workspace
    user_inputs     [str] task texts (agent) or typed lines (repl)
    model_replies   [reply] ordered; one is consumed per model request:
                      {"content": "..."}  and/or
                      {"tool_calls": [{"name": "...", "arguments": {...}}]}
                      optional "finish_reason", "reasoning"
    tool_results    {"<dispatcher call index>" | "*": "text"} fake results
                    instead of really executing (index counts every call that
                    reaches the tool dispatcher, sub-agent calls included)
    approvals       [str] what the user types at each approval prompt
                    ("[y/N]"); once exhausted the answer is "n"
    question_answers [str] what the user types at each ask_user question
    xfail           str   reason; the scenario documents a known-open bug and
                    must currently FAIL (it turns red once the bug is fixed)
    expect          {...} see EXPECT_KEYS below

Implicit invariants checked for every scenario: nothing raised out of the
agent/REPL, the model was never asked for more replies than scripted, every
scripted reply and user input was consumed, no unexpected input() prompt, and
(repl kind) the loop never printed "Beklenmeyen hata".

`expect` keys::

    final_contains / stdout_contains / stdout_not_contains   str | [str]
        final_contains: last agent reply (agent kind). stdout_*: everything
        printed, whitespace-normalised.
    messages_contain / messages_not_contain   [str]
        non-system message contents the model was shown (all requests,
        sub-agent runs included) plus the final session history.
    executed_tools [name]   ordered tool calls that reached the dispatcher
    executed_count / blocked_count / denied_count / request_count   int
        blocked = repeat-guard results ("TEKRAR ENGELLENDİ"), denied =
        mode-gate results ("REDDEDİLDİ"), request_count = model requests.
    last_tool_choice        tool_choice sent with the last model request
    requests [{"index": i, "tool_choice"?, "tools_include"?, "tools_exclude"?}]
    confirm_prompts / user_questions   int   approvals / ask_user questions asked
    files {path: str | {"absent"?, "contains"?, "equals"?, "no_control_chars"?}}
        paths are relative to the workspace; "@cli_home/<file>" is the CLI home.
"""

from __future__ import annotations

import builtins
import contextlib
import copy
import importlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

DEFAULT_ENV = {
    "EVREN_AUTO_APPROVE": "false",
    "EVREN_AUTO_DOCS": "false",
    "EVREN_AUTO_ROUTE": "false",
}

TOP_LEVEL_KEYS = {
    "name", "description", "kind", "mode", "env", "workspace_files", "user_inputs",
    "model_replies", "tool_results", "approvals", "question_answers", "xfail", "expect",
}
REQUIRED_KEYS = ("name", "description", "user_inputs", "expect")
REPLY_KEYS = {"content", "tool_calls", "finish_reason", "reasoning"}
EXPECT_KEYS = {
    "final_contains", "stdout_contains", "stdout_not_contains",
    "messages_contain", "messages_not_contain",
    "executed_tools", "executed_count", "blocked_count", "denied_count", "request_count",
    "last_tool_choice", "requests", "confirm_prompts", "user_questions", "files",
}
FILE_EXPECT_KEYS = {"absent", "contains", "equals", "no_control_chars"}
REQUEST_EXPECT_KEYS = {"index", "tool_choice", "tools_include", "tools_exclude"}

BLOCKED_PREFIX = "TEKRAR ENGELLENDİ"
DENIED_PREFIX = "REDDEDİLDİ"
_APPROVAL_SUFFIX_RE = re.compile(r"\[(?:Y/n|y/N)\]:\s*$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ScenarioError(ValueError):
    """A scenario file does not follow the schema."""


class ScriptExhausted(RuntimeError):
    """The code under test asked the model for more replies than scripted."""


# --------------------------------------------------------------------------- #
# loading and validation
# --------------------------------------------------------------------------- #

def discover_scenarios() -> list[Path]:
    """All scenario files, sorted by name."""
    return sorted(SCENARIO_DIR.glob("*.json"))


def _strings(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ScenarioError(f"{where}: str or list of str expected")


def validate_scenario(scenario: Any, source: str = "scenario") -> dict:
    """Raises ScenarioError unless `scenario` follows the schema; returns it."""
    if not isinstance(scenario, dict):
        raise ScenarioError(f"{source}: JSON object expected")
    unknown = set(scenario) - TOP_LEVEL_KEYS
    if unknown:
        raise ScenarioError(f"{source}: unknown keys {sorted(unknown)}")
    for key in REQUIRED_KEYS:
        if key not in scenario:
            raise ScenarioError(f"{source}: missing required key '{key}'")
    for key in ("name", "description"):
        if not isinstance(scenario[key], str) or not scenario[key].strip():
            raise ScenarioError(f"{source}: '{key}' must be a non-empty string")
    kind = scenario.get("kind", "agent")
    if kind not in ("agent", "repl"):
        raise ScenarioError(f"{source}: kind must be 'agent' or 'repl'")
    mode = scenario.get("mode", "normal")
    if mode not in ("normal", "ask", "plan"):
        raise ScenarioError(f"{source}: mode must be normal, ask or plan")
    if kind == "repl" and mode != "normal":
        raise ScenarioError(f"{source}: repl scenarios change the mode with a '/mode' input line")
    env = scenario.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ScenarioError(f"{source}: env must map str to str")
    files = scenario.get("workspace_files", {})
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in files.items()
    ):
        raise ScenarioError(f"{source}: workspace_files must map path to text")
    for rel in files:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ScenarioError(f"{source}: workspace_files path '{rel}' must stay inside the workspace")
    inputs = scenario["user_inputs"]
    if not isinstance(inputs, list) or not inputs or not all(isinstance(i, str) for i in inputs):
        raise ScenarioError(f"{source}: user_inputs must be a non-empty list of str")
    replies = scenario.get("model_replies", [])
    if not isinstance(replies, list):
        raise ScenarioError(f"{source}: model_replies must be a list")
    for index, reply in enumerate(replies):
        where = f"{source}: model_replies[{index}]"
        if not isinstance(reply, dict) or set(reply) - REPLY_KEYS:
            raise ScenarioError(f"{where}: object with keys from {sorted(REPLY_KEYS)} expected")
        if not reply.get("content") and not reply.get("tool_calls"):
            raise ScenarioError(f"{where}: needs content or tool_calls")
        for call in reply.get("tool_calls", []):
            if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not isinstance(
                call.get("arguments", {}), dict
            ):
                raise ScenarioError(f"{where}: tool call needs 'name' and object 'arguments'")
    fakes = scenario.get("tool_results", {})
    if not isinstance(fakes, dict) or not all(
        (k == "*" or k.isdigit()) and isinstance(v, str) for k, v in fakes.items()
    ):
        raise ScenarioError(f"{source}: tool_results keys must be call indexes or '*'")
    for key in ("approvals", "question_answers"):
        if not isinstance(scenario.get(key, []), list) or not all(
            isinstance(i, str) for i in scenario.get(key, [])
        ):
            raise ScenarioError(f"{source}: {key} must be a list of str")
    if "xfail" in scenario and (not isinstance(scenario["xfail"], str) or not scenario["xfail"].strip()):
        raise ScenarioError(f"{source}: xfail must be a non-empty reason string")
    _validate_expect(scenario["expect"], kind, source)
    return scenario


def _validate_expect(expect: Any, kind: str, source: str) -> None:
    if not isinstance(expect, dict) or not expect:
        raise ScenarioError(f"{source}: expect must be a non-empty object")
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise ScenarioError(f"{source}: unknown expect keys {sorted(unknown)}")
    if kind == "repl" and "final_contains" in expect:
        raise ScenarioError(f"{source}: final_contains is for agent scenarios; use stdout_contains")
    for key in ("final_contains", "stdout_contains", "stdout_not_contains",
                "messages_contain", "messages_not_contain", "executed_tools"):
        if key in expect:
            _strings(expect[key], f"{source}: expect.{key}")
    for key in ("executed_count", "blocked_count", "denied_count", "request_count",
                "confirm_prompts", "user_questions"):
        if key in expect and (not isinstance(expect[key], int) or isinstance(expect[key], bool)):
            raise ScenarioError(f"{source}: expect.{key} must be an integer")
    for entry in expect.get("requests", []):
        if not isinstance(entry, dict) or "index" not in entry or set(entry) - REQUEST_EXPECT_KEYS:
            raise ScenarioError(
                f"{source}: expect.requests entries need 'index' and keys from {sorted(REQUEST_EXPECT_KEYS)}"
            )
    for rel, spec in expect.get("files", {}).items():
        if isinstance(spec, dict):
            if set(spec) - FILE_EXPECT_KEYS:
                raise ScenarioError(f"{source}: expect.files['{rel}'] keys must be in {sorted(FILE_EXPECT_KEYS)}")
        elif not isinstance(spec, str):
            raise ScenarioError(f"{source}: expect.files['{rel}'] must be a string or an object")


def load_scenario(path: Path) -> dict:
    """Reads and validates one scenario file (the name must match the file name)."""
    path = Path(path)
    try:
        scenario = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{path.name}: invalid JSON: {exc}") from exc
    validate_scenario(scenario, path.name)
    if scenario["name"] != path.stem:
        raise ScenarioError(f"{path.name}: name '{scenario['name']}' must equal the file name")
    return scenario


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #

@dataclass
class ScenarioResult:
    scenario: dict
    workspace: Path
    cli_home: Path
    session: Any = None
    outputs: list[str] = field(default_factory=list)
    exception: BaseException | None = None
    stdout: str = ""
    requests: list[dict] = field(default_factory=list)
    executed: list[dict] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unused_replies: int = 0
    unused_inputs: int = 0

    def messages_shown(self) -> Iterator[dict]:
        """Non-system messages the model was shown, plus the final history."""
        for request in self.requests:
            for message in request["messages"]:
                if message.get("role") != "system":
                    yield message
        if self.session is not None:
            for message in self.session.messages:
                if message.get("role") != "system":
                    yield message

    def shown_text(self) -> str:
        seen: dict[str, None] = {}
        for message in self.messages_shown():
            content = message.get("content")
            if isinstance(content, str) and content:
                seen[content] = None
        return "\n".join(seen)

    def tool_result_texts(self) -> list[str]:
        """One entry per distinct tool call id seen in any request."""
        by_id: dict[Any, str] = {}
        for message in self.messages_shown():
            if message.get("role") == "tool":
                by_id[message.get("tool_call_id") or id(message)] = str(message.get("content") or "")
        return list(by_id.values())

    def normalized_stdout(self) -> str:
        return " ".join(self.stdout.split())

    def read_file(self, rel: str) -> str | None:
        root = self.cli_home if rel.startswith("@cli_home/") else self.workspace
        target = root / (rel[len("@cli_home/"):] if rel.startswith("@cli_home/") else rel)
        if not target.is_file():
            return None
        return target.read_bytes().decode("utf-8", errors="replace")

    def trace(self) -> str:
        """Compact description of what happened, for failure messages."""
        lines = [
            f"model requests: {len(self.requests)} "
            f"(tool_choice: {[r['tool_choice'] for r in self.requests]})",
            f"dispatched tools: {[e['name'] for e in self.executed]}",
            f"approval prompts: {self.approvals}",
            f"ask_user questions: {self.questions}",
            f"outputs: {[o[:120] for o in self.outputs]}",
        ]
        if self.exception is not None:
            lines.append(f"exception: {type(self.exception).__name__}: {self.exception}")
        if self.problems:
            lines.append(f"harness problems: {self.problems}")
        tail = self.stdout[-1200:].strip()
        if tail:
            lines.append("stdout tail:\n" + tail)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #

def _patch_if_present(stack: contextlib.ExitStack, target: str, value: Any) -> bool:
    """Patches `module.attr` when it exists, so a refactor that moves a name
    does not break the harness (the scenario still exercises the new path)."""
    module_name, attr = target.rsplit(".", 1)
    module = importlib.import_module(module_name)
    if not hasattr(module, attr):
        return False
    stack.enter_context(patch.object(module, attr, value))
    return True


class _Runner:
    def __init__(self, scenario: dict, tmp_root: Path):
        self.scenario = scenario
        self.kind = scenario.get("kind", "agent")
        workspace = tmp_root / "workspace"
        cli_home = tmp_root / "cli_home"
        home = tmp_root / "home"
        for directory in (workspace, cli_home, home):
            directory.mkdir(parents=True, exist_ok=True)
        self.home = home
        self.result = ScenarioResult(scenario=scenario, workspace=workspace, cli_home=cli_home)
        self.replies = copy.deepcopy(scenario.get("model_replies", []))
        self.approvals = list(scenario.get("approvals", []))
        self.question_answers = list(scenario.get("question_answers", []))
        self.repl_lines = list(scenario["user_inputs"]) if self.kind == "repl" else []
        self.fake_results = scenario.get("tool_results", {})
        self._call_counter = 0
        self.real_execute = None

    # -- environment ------------------------------------------------------- #

    def build_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("EVREN_")}
        env.update(DEFAULT_ENV)
        env["EVREN_CLI_HOME"] = str(self.result.cli_home)
        env["HOME"] = env["USERPROFILE"] = str(self.home)
        env.update(self.scenario.get("env", {}))
        return env

    # -- fake terminal ----------------------------------------------------- #

    def fake_input(self, prompt: Any = "") -> str:
        text = str(prompt)
        if "[Model sorusu]" in text:
            self.result.questions.append(text.strip())
            return self.question_answers.pop(0) if self.question_answers else ""
        if _APPROVAL_SUFFIX_RE.search(text):
            self.result.approvals.append(text.strip())
            return self.approvals.pop(0) if self.approvals else "n"
        if self.kind == "repl":
            if not self.repl_lines:
                raise EOFError
            return self.repl_lines.pop(0)
        self.result.problems.append(f"unexpected input() prompt: {text.strip()[:120]!r}")
        raise EOFError

    # -- fake tool dispatcher ---------------------------------------------- #

    def dispatch(self, *args: Any, **kwargs: Any) -> str:
        name = kwargs["name"] if "name" in kwargs else args[0]
        arguments = kwargs["arguments"] if "arguments" in kwargs else args[1]
        index = len(self.result.executed)
        fake = self.fake_results.get(str(index), self.fake_results.get("*"))
        if fake is not None:
            output = fake
        else:
            output = self.real_execute(*args, **kwargs)
        self.result.executed.append({
            "index": index, "name": name, "arguments": copy.deepcopy(arguments),
            "result": output, "faked": fake is not None,
        })
        return output

    # -- fake model -------------------------------------------------------- #

    def build_client(self) -> MagicMock:
        client = MagicMock(name="scripted_client")
        client.default_model = "test-model"
        client.api_key = "test-key"
        client.key_pool = []
        client.reasoning_effort = None
        client.get_quota.return_value = {"remaining": 10, "limit": 100}
        client.chat_stream_assembled.side_effect = self.chat
        return client

    def chat(self, **kwargs: Any) -> Any:
        self.result.requests.append({
            "model": kwargs.get("model"),
            "tool_choice": kwargs.get("tool_choice"),
            "thinking": kwargs.get("thinking"),
            "tools": [t.get("function", {}).get("name") for t in kwargs.get("tools") or []],
            "messages": copy.deepcopy(list(kwargs.get("messages") or [])),
        })
        if not self.replies:
            message = (
                f"model_replies exhausted: request #{len(self.result.requests)} has no scripted reply"
            )
            self.result.problems.append(message)
            raise ScriptExhausted(message)
        reply = self.replies.pop(0)
        content = reply.get("content", "")
        calls = []
        for spec in reply.get("tool_calls", []):
            self._call_counter += 1
            calls.append(SimpleNamespace(
                id=spec.get("id") or f"call_{self._call_counter}",
                function=SimpleNamespace(
                    name=spec["name"],
                    arguments=json.dumps(spec.get("arguments", {}), ensure_ascii=False),
                ),
            ))
        # The real client streams text through these callbacks before it
        # returns the assembled response; mirror that so the UI path runs too.
        if reply.get("reasoning") and kwargs.get("on_reasoning"):
            kwargs["on_reasoning"](reply["reasoning"])
        if content and kwargs.get("on_content"):
            kwargs["on_content"](content)
        finish = reply.get("finish_reason") or ("tool_calls" if calls else "stop")
        message = SimpleNamespace(content=content, reasoning="", tool_calls=calls)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish, message=message)], usage=None,
        )

    # -- run --------------------------------------------------------------- #

    def run(self) -> ScenarioResult:
        result = self.result
        for rel, text in self.scenario.get("workspace_files", {}).items():
            target = result.workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")

        from src import background, session_context, secret_vault, tools as tools_module, ui
        from src.todos import get_store

        self.real_execute = tools_module.execute_tool_call
        env = self.build_env()
        auto_approve = env.get("EVREN_AUTO_APPROVE", "false").strip().lower() in ("true", "1", "yes")
        stdout = io.StringIO()

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            # Read at import time from the developer's .env (often "true").
            _patch_if_present(stack, "src.config.EVREN_AUTO_APPROVE", auto_approve)
            _patch_if_present(stack, "src.tools.EVREN_AUTO_APPROVE", auto_approve)
            stack.enter_context(patch.object(builtins, "input", self.fake_input))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            patched = [
                _patch_if_present(stack, "src.agent.execute_tool_call", self.dispatch),
                _patch_if_present(stack, "src.tools.execute_tool_call", self.dispatch),
            ]
            if not any(patched):
                result.problems.append("no execute_tool_call to wrap; update the harness")

            # Process-wide state a previous test may have left behind.
            session_context.current().background = None
            secret_vault.set_active_vault(secret_vault.SecretVault())
            get_store().clear()
            ui._STREAM.update(buffer=[], live=None, pending="")
            try:
                if self.kind == "repl":
                    self._run_repl(stack)
                else:
                    self._run_agent()
            except (Exception, SystemExit) as exc:
                result.exception = exc
            finally:
                try:
                    if session_context.current().background is not None:
                        session_context.current().background.cleanup_all()
                except Exception:
                    pass
                session_context.current().background = None
                secret_vault.set_active_vault(secret_vault.SecretVault())
                get_store().clear()
                ui._STREAM.update(buffer=[], live=None, pending="")

        result.stdout = stdout.getvalue()
        result.unused_replies = len(self.replies)
        result.unused_inputs = len(self.repl_lines) if self.kind == "repl" else (
            len(self.scenario["user_inputs"]) - len(result.outputs)
        )
        return result

    def _run_agent(self) -> None:
        from src.agent import AgentSession
        from src.modes import AgentMode

        session = AgentSession(self.build_client(), workspace_root=self.result.workspace)
        self.result.session = session
        mode = self.scenario.get("mode", "normal")
        if mode != "normal":
            session.set_mode(AgentMode(mode))
        for text in self.scenario["user_inputs"]:
            self.result.outputs.append(session.run_step(text))

    def _run_repl(self, stack: contextlib.ExitStack) -> None:
        from src import repl

        client = self.build_client()
        session_class = repl.AgentSession

        def make_session(*args: Any, **kwargs: Any) -> Any:
            session = session_class(*args, **kwargs)
            self.result.session = session
            return session

        stack.enter_context(patch.object(repl, "EvrenClient", return_value=client))
        stack.enter_context(patch.object(repl, "ensure_terms_accepted", return_value=True))
        stack.enter_context(patch.object(repl, "HAVE_PT", False))
        stack.enter_context(patch.object(repl, "AgentSession", make_session))
        repl.start_repl(workspace_path=str(self.result.workspace))


def run_scenario(scenario: dict, tmp_root: Path) -> ScenarioResult:
    """Runs a validated scenario inside `tmp_root` (an existing, empty directory)."""
    return _Runner(scenario, Path(tmp_root)).run()


# --------------------------------------------------------------------------- #
# expectations
# --------------------------------------------------------------------------- #

def check_expectations(result: ScenarioResult) -> list[str]:
    """Returns human-readable failures; an empty list means the scenario passed."""
    failures: list[str] = []
    scenario = result.scenario
    expect = scenario["expect"]

    # implicit invariants
    if result.exception is not None:
        failures.append(f"raised {type(result.exception).__name__}: {result.exception}")
    failures.extend(f"harness: {problem}" for problem in result.problems)
    if result.unused_replies:
        failures.append(f"{result.unused_replies} scripted model reply(ies) never consumed")
    if result.unused_inputs:
        failures.append(f"{result.unused_inputs} scripted user input(s) never consumed")
    if scenario.get("kind", "agent") == "repl" and "Beklenmeyen hata" in result.stdout:
        failures.append("the REPL loop swallowed an error ('Beklenmeyen hata' was printed)")

    tool_results = result.tool_result_texts()
    counts = {
        "executed_count": len(result.executed),
        "blocked_count": sum(1 for t in tool_results if t.startswith(BLOCKED_PREFIX)),
        "denied_count": sum(1 for t in tool_results if t.startswith(DENIED_PREFIX)),
        "request_count": len(result.requests),
        "confirm_prompts": len(result.approvals),
        "user_questions": len(result.questions),
    }
    for key, actual in counts.items():
        if key in expect and expect[key] != actual:
            failures.append(f"{key}: expected {expect[key]}, got {actual}")

    if "executed_tools" in expect:
        actual_names = [e["name"] for e in result.executed]
        if actual_names != list(expect["executed_tools"]):
            failures.append(f"executed_tools: expected {expect['executed_tools']}, got {actual_names}")

    if "last_tool_choice" in expect:
        if not result.requests:
            failures.append("last_tool_choice: no model request was made")
        elif result.requests[-1]["tool_choice"] != expect["last_tool_choice"]:
            failures.append(
                f"last_tool_choice: expected {expect['last_tool_choice']!r}, "
                f"got {result.requests[-1]['tool_choice']!r}"
            )

    for entry in expect.get("requests", []):
        index = entry["index"]
        try:
            request = result.requests[index]
        except IndexError:
            failures.append(f"requests[{index}]: only {len(result.requests)} model request(s) were made")
            continue
        if "tool_choice" in entry and request["tool_choice"] != entry["tool_choice"]:
            failures.append(
                f"requests[{index}].tool_choice: expected {entry['tool_choice']!r}, got {request['tool_choice']!r}"
            )
        for name in entry.get("tools_include", []):
            if name not in request["tools"]:
                failures.append(f"requests[{index}]: tool '{name}' was not offered")
        for name in entry.get("tools_exclude", []):
            if name in request["tools"]:
                failures.append(f"requests[{index}]: tool '{name}' was offered")

    if "final_contains" in expect:
        final = result.outputs[-1] if result.outputs else ""
        for needle in _strings(expect["final_contains"], "final_contains"):
            if needle not in final:
                failures.append(f"final_contains: {needle!r} not in the final reply {final[:200]!r}")

    stdout = result.normalized_stdout()
    for needle in _strings(expect.get("stdout_contains", []), "stdout_contains"):
        if " ".join(needle.split()) not in stdout:
            failures.append(f"stdout_contains: {needle!r} was not printed")
    for needle in _strings(expect.get("stdout_not_contains", []), "stdout_not_contains"):
        if " ".join(needle.split()) in stdout:
            failures.append(f"stdout_not_contains: {needle!r} was printed")

    shown = result.shown_text()
    for needle in expect.get("messages_contain", []):
        if needle not in shown:
            failures.append(f"messages_contain: {needle!r} never reached the model")
    for needle in expect.get("messages_not_contain", []):
        if needle in shown:
            failures.append(f"messages_not_contain: {needle!r} reached the model")

    for rel, spec in expect.get("files", {}).items():
        failures.extend(_check_file(result, rel, spec))
    return failures


def _check_file(result: ScenarioResult, rel: str, spec: Any) -> list[str]:
    if isinstance(spec, str):
        spec = {"contains": spec}
    text = result.read_file(rel)
    if spec.get("absent"):
        return [f"files['{rel}']: expected absent but it exists"] if text is not None else []
    if text is None:
        return [f"files['{rel}']: file does not exist"]
    problems: list[str] = []
    for needle in _strings(spec.get("contains", []), f"files['{rel}'].contains"):
        if needle not in text:
            problems.append(f"files['{rel}']: missing {needle!r}")
    if "equals" in spec and text != spec["equals"]:
        problems.append(f"files['{rel}']: content is {text!r}, expected {spec['equals']!r}")
    if spec.get("no_control_chars"):
        found = sorted({f"U+{ord(c):04X}" for c in _CONTROL_CHARS_RE.findall(text)})
        if found:
            problems.append(f"files['{rel}']: contains control characters {found}")
    return problems


def run_and_check(scenario: dict) -> tuple[ScenarioResult, list[str]]:
    """Runs a scenario in a throw-away directory; returns (result, failures)."""
    with tempfile.TemporaryDirectory(prefix="evren-scn-", ignore_cleanup_errors=True) as directory:
        result = run_scenario(scenario, Path(directory))
        return result, check_expectations(result)
