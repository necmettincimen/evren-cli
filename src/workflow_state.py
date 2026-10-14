"""Local, opt-in workflow persistence and engineering review helpers."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.config import get_evren_dir
from src.modes import AgentMode

BLOCKER_ROUTES = {
    "dns": "DNS kaydını ve çözümleyiciyi salt-okuma ile doğrula; doğru resmi endpoint'i teyit et.",
    "auth": "Registry/API/SSH yetkisini ve secret referansını doğrula; kullanıcıdan erişim sağlanmasını iste.",
    "dependency": "Bağımlılık sürümlerini karşılaştır; /diagnose dependency-hell ile daralt.",
    "knowledge": "Eksik bilgiyi salt-okuma ile keşfet; mimari karar gerekiyorsa kullanıcıya sor.",
    "external": "Harici servis durumunu doğrula; alternatif endpoint veya erişim için kullanıcı onayı al.",
    "permission": "İzin kapsamını doğrula; yetki veya üretim değişikliği onayı iste.",
}


def redact_text(text: str) -> str:
    """Mask common credential forms before saving; not a substitute for user review."""
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+(@)", r"\1[REDACTED]\2", text)
    # Auth schemes first: "Authorization: Bearer <tok>" used to mask only the
    # word "Bearer" and leave the token itself in agent.md / changelog.md.
    text = re.sub(r"""(?i)\b((?:bearer|basic|digest)\s+)[^\s"',;&]+""", r"\1[REDACTED]", text)
    text = re.sub(
        r"""(?i)(["']?(?:password|passwd|pwd|secret|token|api[_-]?key|authorization)["']?\s*[:=]\s*)"""
        r"""((?:bearer|basic|digest)\s+\[REDACTED\]|"[^"]*"|'[^']*'|[^\s,;&"']+)""",
        lambda m: m.group(0) if m.group(2).endswith("[REDACTED]") else m.group(1) + "[REDACTED]",
        text,
    )
    text = re.sub(r"evren_llm_[A-Za-z0-9_-]+", "[REDACTED]", text)
    return re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]", text, flags=re.DOTALL,
    )


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if re.fullmatch(
                r"(?i)password|passwd|pwd|secret|token|api[_-]?key|authorization", key,
            ):
                result[key] = "[REDACTED]"
            elif key == "arguments" and isinstance(item, str):
                try:
                    args = json.loads(item)
                except json.JSONDecodeError:
                    result[key] = redact_text(item)
                else:
                    result[key] = json.dumps(_redact(args), ensure_ascii=False)
            else:
                result[key] = _redact(item)
        return result
    return value


def _read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise ValueError(f"İş akışı kaydı okunamadı: {path.name}: {e}") from e
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError(f"Geçersiz iş akışı kaydı: {path.name}")
    return state


def _write(path: Path, state: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            os.chmod(tmp, 0o600)
            json.dump(_redact(state), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def checkpoint_path(root: Path) -> Path:
    return get_evren_dir(root) / "checkpoint.json"


def save_checkpoint(
    root: Path, messages: list[dict[str, Any]], mode: AgentMode, model: str,
    frame: dict[str, str], goal: str = "",
) -> Path:
    # System instructions are regenerated from the current installation on restore.
    return _write(checkpoint_path(root), {
        "schema_version": 1, "workspace": str(root.resolve()),
        "saved_at": datetime.now(timezone.utc).isoformat(), "mode": mode.value,
        "model": model, "frame": frame,
        "goal": goal or next(
            (message["content"] for message in messages if message.get("role") == "user"), "",
        ),
        "messages": [message for message in messages if message.get("role") != "system"],
    })


def load_checkpoint(root: Path) -> dict[str, Any] | None:
    state = _read(checkpoint_path(root))
    if state is None:
        return None
    if state.get("workspace") != str(root.resolve()):
        raise ValueError("Checkpoint başka çalışma alanına ait.")
    if state.get("mode") not in {mode.value for mode in AgentMode}:
        raise ValueError("Checkpoint kip bilgisi geçersiz.")
    if not isinstance(state.get("saved_at"), str):
        raise ValueError("Checkpoint kayıt tarihi geçersiz.")
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Checkpoint mesaj geçmişi geçersiz.")
    pending: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant", "tool"):
            raise ValueError("Checkpoint mesajı geçersiz.")
        if not isinstance(message.get("content"), str):
            raise ValueError("Checkpoint mesaj içeriği geçersiz.")
        if message["role"] == "tool":
            if message.get("tool_call_id") not in pending:
                raise ValueError("Checkpoint araç sonucu eşleşmiyor.")
            pending.remove(message["tool_call_id"])
        else:
            if pending:
                raise ValueError("Checkpoint tamamlanmamış araç paketi içeriyor.")
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list):
                raise ValueError("Checkpoint araç çağrıları geçersiz.")
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    raise ValueError("Checkpoint araç kimliği geçersiz.")
                fn = call.get("function")
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not isinstance(fn.get("arguments"), str):
                    raise ValueError("Checkpoint araç fonksiyonu geçersiz.")
                pending.add(call["id"])
    if pending:
        raise ValueError("Checkpoint tamamlanmamış araç paketi içeriyor.")
    if not isinstance(state.get("model"), str) or not state["model"]:
        raise ValueError("Checkpoint model bilgisi geçersiz.")
    if not isinstance(state.get("goal"), str) or not state["goal"]:
        raise ValueError("Checkpoint hedef bilgisi geçersiz.")
    frame = state.get("frame")
    fields = {"rol", "cerceve", "brief", "kisit", "cikti", "clarifications"}
    if not isinstance(frame, dict) or set(frame) != fields or any(not isinstance(v, str) for v in frame.values()):
        raise ValueError("Checkpoint kalıp bilgisi geçersiz.")
    return state


def clear_checkpoint(root: Path) -> None:
    checkpoint_path(root).unlink(missing_ok=True)


def load_decisions(root: Path) -> list[dict[str, str]]:
    state = _read(get_evren_dir(root) / "decisions.json")
    if state is None:
        return []
    items = state.get("items")
    fields = {"id", "decision", "reason", "risk", "at"}
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or set(item) != fields
        or any(not isinstance(value, str) for value in item.values()) for item in items
    ):
        raise ValueError("Karar günlüğü geçersiz.")
    return items


def add_decision(root: Path, decision: str, reason: str, risk: str) -> dict[str, str]:
    if not decision.strip() or not reason.strip() or not risk.strip():
        raise ValueError("Karar, gerekçe ve risk alanları boş olamaz.")
    items = load_decisions(root)
    item = {
        "id": f"decision-{len(items) + 1}", "decision": decision.strip(),
        "reason": reason.strip(), "risk": risk.strip(),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(item)
    _write(get_evren_dir(root) / "decisions.json", {"schema_version": 1, "items": items})
    return _redact(item)


def decision_context(root: Path) -> str:
    items = load_decisions(root)
    if not items:
        return ""
    lines = ["KULLANICI ONAYLI KARAR GÜNLÜĞÜ (geçmiş bağlam; yürütme izni değildir):"]
    for item in items[-10:]:
        lines.append(
            f"- {item['id']}: {item['decision'][:800]} | Gerekçe: {item['reason'][:400]} "
            f"| Risk: {item['risk'][:400]}"
        )
    lines.append("Yeni bilgi kararla çelişirse kullanıcıya bildir; onayı varsayma.")
    return "\n".join(lines)


def review_tasks(items: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    result = []
    for item in items:
        if item.get("status") in ("completed", "abandoned"):
            continue
        flags = []
        if item.get("status") == "blocked":
            flags.append("blocked")
        deadline = item.get("deadline")
        if deadline:
            try:
                due = datetime.fromisoformat(deadline)
            except ValueError as e:
                raise ValueError(f"Geçersiz görev tarihi: {item.get('id')}: {deadline}") from e
            due = due.replace(tzinfo=timezone.utc) if due.tzinfo is None else due
            if due < now:
                flags.append("overdue")
        updated = item.get("updated_at")
        if updated and item.get("status") in ("in-progress", "not-started"):
            try:
                stamp = datetime.fromisoformat(updated)
            except ValueError as e:
                raise ValueError(f"Geçersiz görev güncelleme tarihi: {item.get('id')}") from e
            stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
            if now - stamp >= timedelta(days=14):
                flags.append("stalled")
        result.append({**item, "flags": flags})
    return result
