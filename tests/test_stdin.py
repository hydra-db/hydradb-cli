"""Tests for piped-stdin input handling (``read_stdin_safe``).

The pipe is the CLI's agent-facing entry point (``… | hydradb ingest``), so the
cases below pin the two ways a readiness poll used to discard input silently: a
producer slower than the poll timeout, and a stdin handle that ``select`` cannot
accept at all on Windows.
"""

import io
import os
import sys
import threading
import time

from hydradb_cli.utils.common import read_stdin_safe


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def _pipe_stdin(monkeypatch, payload: bytes, delay: float = 0.0):
    """Point ``sys.stdin`` at a real OS pipe fed by a background writer.

    A real pipe (rather than a StringIO) is what makes this a regression test:
    it is the handle shape that a readiness poll mishandles.
    """
    read_fd, write_fd = os.pipe()

    def _write_later() -> None:
        time.sleep(delay)
        os.write(write_fd, payload)
        os.close(write_fd)

    writer = threading.Thread(target=_write_later, daemon=True)
    writer.start()
    monkeypatch.setattr(sys, "stdin", os.fdopen(read_fd, "r"))
    return writer


class TestReadStdinSafe:
    def test_returns_none_for_interactive_terminal(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", _FakeTTY("typed at a prompt"))
        assert read_stdin_safe() is None

    def test_reads_piped_text(self, monkeypatch):
        writer = _pipe_stdin(monkeypatch, b"piped note")
        assert read_stdin_safe() == "piped note"
        writer.join()

    def test_waits_for_a_slow_producer(self, monkeypatch):
        """Input must survive a producer slower than any poll timeout.

        ``curl … | hydradb ingest`` writes nothing for as long as the request
        takes; polling stdin for readiness dropped that note on the floor.
        """
        writer = _pipe_stdin(monkeypatch, b"slow note", delay=0.3)
        assert read_stdin_safe() == "slow note"
        writer.join()

    def test_reads_multiline_payload_in_full(self, monkeypatch):
        writer = _pipe_stdin(monkeypatch, b"first line\nsecond line\n")
        assert read_stdin_safe() == "first line\nsecond line"
        writer.join()

    def test_returns_none_for_empty_pipe(self, monkeypatch):
        writer = _pipe_stdin(monkeypatch, b"")
        assert read_stdin_safe() is None
        writer.join()

    def test_returns_none_for_whitespace_only_pipe(self, monkeypatch):
        writer = _pipe_stdin(monkeypatch, b"   \n\t\n")
        assert read_stdin_safe() is None
        writer.join()

    def test_returns_none_when_stdin_is_detached(self, monkeypatch):
        """A GUI/pythonw launch leaves ``sys.stdin`` as None rather than a stream."""
        monkeypatch.setattr(sys, "stdin", None)
        assert read_stdin_safe() is None

    def test_returns_none_when_stdin_is_closed(self, monkeypatch):
        closed = io.StringIO("x")
        closed.close()
        monkeypatch.setattr(sys, "stdin", closed)
        assert read_stdin_safe() is None
