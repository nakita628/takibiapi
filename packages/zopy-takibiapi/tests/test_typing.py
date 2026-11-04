"""Runs pyright over ``tests/typing`` (route / handler type expectations) when pyright is installed
(``uv sync`` installs it)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TYPING_DIR = Path(__file__).parent / "typing"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright not installed")
def test_route_typing_expectations_hold() -> None:
    proc = subprocess.run(
        ["pyright", "--project", str(REPO_ROOT), str(TYPING_DIR)],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
