import re

import pytest
from typer.testing import CliRunner

from hydradb_cli.main import app


@pytest.mark.parametrize(
    "command, option, next_option",
    [
        (["ingest"], "--infer", "--markdown"),
        (["ingest"], "--enrich", "--instructions"),
        (["memories", "add"], "--infer", "--markdown"),
    ],
    ids=["canonical-infer", "canonical-enrich", "legacy-infer"],
)
def test_enrichment_help_does_not_promise_disabling_graph_processing(command, option, next_option):
    result = CliRunner().invoke(app, [*command, "--help"], env={"COLUMNS": "240", "TERM": "dumb", "NO_COLOR": "1"})
    assert result.exit_code == 0, result.output
    plain = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.stdout).split()).lower()
    section = plain.split(option, 1)[1].split(next_option, 1)[0]
    assert "additional memory enrichment" in section
    assert "disabling it leaves entity/graph processing enabled" in section
    assert "build knowledge graph" not in section
    assert "extract facts and graph relations" not in section
    assert "raw-only storage" not in section and "no interpretation" not in section
