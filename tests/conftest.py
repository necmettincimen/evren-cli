"""Test-wide defaults: no live classifier calls from unit tests."""

import os

os.environ["EVREN_CLEF_INTENT"] = "false"
os.environ["EVREN_SEMANTIC_SEARCH"] = "false"
