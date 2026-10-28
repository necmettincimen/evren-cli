"""Test-wide defaults: no live classifier calls and no real agent retry waits in unit tests."""

import os
import time

import pytest

os.environ["EVREN_CLEF_INTENT"] = "false"
os.environ["EVREN_SEMANTIC_SEARCH"] = "false"


class _NoSleepTime:
    """`time` module stand-in for src.agent: sleep is a no-op, everything else is real."""

    def sleep(self, seconds):
        return None

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture(autouse=True)
def _no_agent_retry_sleep(monkeypatch):
    # Transient API errors back off 3/6/12/24 s in AgentSession.run_step; tests only care about
    # the retry count, so the wait is skipped (only inside src.agent, time.sleep elsewhere stays).
    import src.agent

    monkeypatch.setattr(src.agent, "time", _NoSleepTime())
