"""Regression tests for config robustness, stdin handling, and raw-path errors."""

import json
import os
import stat
import subprocess
import sys
import textwrap
import time

import httpx
import pytest

from hydradb_cli import config
from hydradb_cli.hydra.client import _checked_body
from hydradb_cli.hydra.errors import HydraDBClientError
from hydradb_cli.utils.common import mask_api_key


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_warned_bad_config", False)
    for var in ("HYDRADB_API_KEY", "HYDRA_DB_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "config.json"


@pytest.mark.parametrize("content", [b"[]", b"null", b'"str"', b"42", b'{"api_key": "x', b'{"a": "caf\xe9"}'])
def test_bad_config_never_crashes(cfg, content, capsys):
    cfg.write_bytes(content)
    assert config.get_api_key() is None
    config.save_config(api_key="sk-new")  # login must be able to recover
    assert config.get_api_key() == "sk-new"
    assert "ignoring unreadable config" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_config_written_0600_atomically(cfg):
    old = os.umask(0o022)
    try:
        config.save_config(api_key="sk-secret-value")
    finally:
        os.umask(old)
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    assert json.loads(cfg.read_text())["api_key"] == "sk-secret-value"
    assert [p.name for p in cfg.parent.iterdir()] == ["config.json"]  # no temp file left


@pytest.mark.parametrize("n", [1, 8, 13, 16, 20, 40])
def test_mask_never_reveals_most_of_key(n):
    key = "".join(chr(65 + i % 26) for i in range(n))
    masked = mask_api_key(key)
    revealed = len(masked.replace("...", "").replace("*", ""))
    assert revealed <= n // 2


def test_explicit_stdin_waits_for_slow_producer():
    code = textwrap.dedent(
        """
        import sys, time
        from hydradb_cli.commands.canonical import _resolve_text_input
        print(_resolve_text_input("-"))
        """
    )
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    time.sleep(0.6)
    out, _ = proc.communicate("late data\n", timeout=10)
    assert out.strip() == "late data"


def _resp(status, **kw):
    return httpx.Response(status, request=httpx.Request("GET", "http://x"), **kw)


def test_raw_path_rejects_redirect():
    with pytest.raises(HydraDBClientError) as ei:
        _checked_body(_resp(301, headers={"Location": "https://elsewhere"}))
    assert ei.value.status_code == 301


def test_raw_path_rejects_non_json_success():
    with pytest.raises(HydraDBClientError):
        _checked_body(_resp(200, text="<html>captive portal</html>"))


def test_raw_path_accepts_json_and_empty():
    assert _checked_body(_resp(200, json={"a": 1})) == {"a": 1}
    assert _checked_body(_resp(204)) is None
