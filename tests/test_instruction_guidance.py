from pathlib import Path

import pytest
from typer.testing import CliRunner

from hydradb_cli.main import app


@pytest.mark.parametrize("width", [80, 240])
def test_ingest_instruction_carrier_is_hidden_from_public_help(width, monkeypatch):
    monkeypatch.setattr("typer.rich_utils.MAX_WIDTH", width)
    result = CliRunner().invoke(app, ["ingest", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert result.exit_code == 0, result.output
    assert "--instructions" not in result.stdout
    assert "--enrich" in result.stdout
    assert "--happened-at" in result.stdout


def test_current_public_guidance_does_not_advertise_instruction_carrier():
    root = Path(__file__).resolve().parents[1]
    changelog = (root / "CHANGELOG.md").read_text()
    current = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "--instructions" not in current
    assert "4000 characters of instructions" not in current
    assert "--instructions" not in (root / "README.md").read_text()
