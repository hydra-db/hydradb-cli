"""Integration tests for the ``hydradb connectors`` commands.

Commands reach the connector endpoints through ``wrapper.connectors``; these
patch the ``get_wrapper`` seam the connectors module imports, so no HTTP
happens.

The credential-handling tests are the important ones: this surface exists to
move secrets around, so "never in argv" and "never echoed" are asserted rather
than assumed.
"""

import json
import re
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import hydradb_cli.config
import hydradb_cli.output
from hydradb_cli.config import save_config
from hydradb_cli.main import app

runner = CliRunner()

_HYDRA_ENV_VARS = (
    "HYDRADB_API_KEY",
    "HYDRADB_DATABASE",
    "HYDRADB_COLLECTION",
    "HYDRADB_BASE_URL",
    "HYDRADB_OUTPUT",
    "HYDRADB_CONNECTOR_CREDENTIALS",
    "HYDRADB_TENANT_ID",
    "HYDRADB_SUB_TENANT_ID",
    "HYDRA_DB_API_KEY",
    "HYDRA_DB_TENANT_ID",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_WIDE = {"COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}

# The real shape of a Slack credential schema, taken from the live API.
SLACK_SCHEMA = {
    "provider": "slack",
    "indexed_object_types": ["conversation_history"],
    "credential_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["access_token"],
        "properties": {
            "access_token": {
                "type": "string",
                "format": "password",
                "description": "Slack bot or user OAuth token (starts with xoxb- or xoxp-).",
            }
        },
    },
    "filterable_fields": [
        {
            "name": "channel_id",
            "data_type": "string",
            "filter_key": "additional_metadata.channel_id",
            "description": "Slack channel id.",
        }
    ],
    "searchable_fields": [{"name": "body", "data_type": "string", "description": "Indexed text."}],
}


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".hydradb"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_dir / "config.json")
    for var in _HYDRA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    hydradb_cli.output._warned_deprecations.clear()
    hydradb_cli.config._warned_env_aliases.clear()
    yield


def _auth():
    save_config(api_key="test-key-abcdef1234567890", tenant_id="t1")


def _wrapper(**overrides):
    w = MagicMock()
    w.default_database = "t1"
    w.default_collection = None
    w.connectors.providers.return_value = overrides.get(
        "providers", [{"provider": "slack", "category": "messaging", "supported": True}]
    )
    w.connectors.provider.return_value = overrides.get("provider", SLACK_SCHEMA)
    w.connectors.list.return_value = overrides.get("list", [])
    w.connectors.get.return_value = overrides.get("get", {"connector_id": "c1", "provider": "slack"})
    w.connectors.create.return_value = overrides.get("create", {"connector_id": "c1"})
    w.connectors.discover.return_value = overrides.get("discover", {"resources": []})
    w.connectors.configure.return_value = {}
    w.connectors.resources.return_value = overrides.get("resources", [])
    w.connectors.sync.return_value = {}
    w.connectors.rotate_credentials.return_value = {}
    w.connectors.delete.return_value = {}
    return w


def _patch(w):
    return patch("hydradb_cli.commands.connectors.get_wrapper", return_value=w)


def _text(result) -> str:
    return re.sub(r"\s+", " ", _ANSI_RE.sub("", result.output))


# ── credentials: never in argv, never echoed ─────────────────────────────────


def test_there_is_no_credentials_flag():
    """A secret in argv lands in shell history and is visible via `ps`.

    Neither is something the user can undo afterwards, so the flag must not
    exist at all — not merely be discouraged.
    """
    for argv in (["connectors", "create"], ["connectors", "rotate-credentials"]):
        result = runner.invoke(app, [*argv, "--help"], env=_WIDE)
        flat = re.sub(r"\s+", " ", _ANSI_RE.sub("", result.output))
        assert "--credentials-stdin" in flat
        assert "--credentials " not in flat
        assert "--credentials=" not in flat


def test_credentials_are_read_from_stdin():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["connectors", "create", "--provider", "slack", "--credentials-stdin"],
            input='{"access_token": "xoxb-secret-value"}',
            env=_WIDE,
        )

    assert result.exit_code == 0
    assert w.connectors.create.call_args.kwargs["credentials"] == {"access_token": "xoxb-secret-value"}


def test_credentials_are_read_from_the_environment(monkeypatch):
    _auth()
    monkeypatch.setenv("HYDRADB_CONNECTOR_CREDENTIALS", '{"access_token": "xoxb-from-env"}')
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "create", "--provider", "slack"], env=_WIDE)

    assert result.exit_code == 0
    assert w.connectors.create.call_args.kwargs["credentials"] == {"access_token": "xoxb-from-env"}


def test_the_credential_value_is_never_echoed():
    """Not in the human output, and not in --output json either."""
    _auth()
    secret = "xoxb-super-secret-value"
    w = _wrapper(create={"connector_id": "c1", "credentials": {"access_token": secret}})
    with _patch(w):
        result = runner.invoke(
            app,
            ["--output", "json", "connectors", "create", "--provider", "slack", "--credentials-stdin"],
            input=json.dumps({"access_token": secret}),
            env=_WIDE,
        )

    assert result.exit_code == 0
    assert secret not in result.output
    assert "<redacted>" in result.output


def test_a_missing_required_credential_field_is_named_locally():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["connectors", "create", "--provider", "slack", "--credentials-stdin"],
            input='{"wrong_field": "x"}',
            env=_WIDE,
        )

    assert result.exit_code == 1
    assert "access_token" in _text(result)
    w.connectors.create.assert_not_called()


def test_malformed_credentials_json_is_rejected():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["connectors", "create", "--provider", "slack", "--credentials-stdin"],
            input="{not json",
            env=_WIDE,
        )

    assert result.exit_code == 1
    w.connectors.create.assert_not_called()


def test_unknown_provider_is_rejected_before_asking_for_credentials():
    _auth()
    w = _wrapper(provider={})
    with _patch(w):
        result = runner.invoke(app, ["connectors", "create", "--provider", "nope"], env=_WIDE)

    assert result.exit_code == 1
    assert "Unknown provider" in _text(result)
    w.connectors.create.assert_not_called()


# ── redaction ────────────────────────────────────────────────────────────────


def test_redaction_preserves_the_public_credential_schema():
    """The schema describes which fields are needed — it is not itself a secret.

    Blanking it removed the one thing `connectors providers <name>` exists to
    show, while protecting nothing: the real secret is always a leaf value.
    """
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "providers", "slack"], env=_WIDE)

    text = _text(result)
    assert "access_token" in text
    assert "xoxb-" in text  # the field's description survives


def test_redaction_does_not_eat_filter_keys():
    """`filter_key` contains "key" but is metadata a user needs to write filters."""
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "providers", "slack"], env=_WIDE)

    assert "additional_metadata.channel_id" in _text(result)


def test_the_redaction_placeholder_is_not_rich_markup():
    """Rich parses [square brackets] as markup and would swallow the placeholder.

    A swallowed placeholder renders as an empty cell, which reads as "absent"
    rather than "hidden".
    """
    from hydradb_cli.commands.connectors import REDACTED, _redact

    assert not REDACTED.startswith("[")
    assert _redact({"access_token": "s"})["access_token"] == REDACTED


def test_redaction_reaches_nested_values():
    from hydradb_cli.commands.connectors import REDACTED, _redact

    out = _redact({"outer": [{"client_secret": "s", "safe": "keep"}]})
    assert out["outer"][0]["client_secret"] == REDACTED
    assert out["outer"][0]["safe"] == "keep"


# ── providers ────────────────────────────────────────────────────────────────


def test_the_provider_catalogue_comes_from_the_api():
    """Never hardcoded: the catalogue gains providers without a CLI release."""
    _auth()
    w = _wrapper(
        providers=[
            {"provider": "brand-new", "category": "crm", "supported": True},
            {"provider": "unsupported-one", "category": "crm", "supported": False},
        ]
    )
    with _patch(w):
        result = runner.invoke(app, ["connectors", "providers"], env=_WIDE)

    assert "brand-new" in _text(result)
    # --supported is the default, so an unsupported provider is filtered out.
    assert "unsupported-one" not in _text(result)


def test_providers_can_be_filtered_by_category():
    _auth()
    w = _wrapper(
        providers=[
            {"provider": "slack", "category": "messaging", "supported": True},
            {"provider": "jira", "category": "project_management", "supported": True},
        ]
    )
    with _patch(w):
        result = runner.invoke(app, ["connectors", "providers", "--category", "messaging"], env=_WIDE)

    assert "slack" in _text(result)
    assert "jira" not in _text(result)


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_list_renders_sync_state():
    _auth()
    w = _wrapper(
        list=[
            {
                "connector_id": "c1",
                "name": "prod",
                "provider": "slack",
                "database": "db",
                "sync_status": "idle",
                "last_successful_sync_at": "2026-08-17T18:30:05Z",
            }
        ]
    )
    with _patch(w):
        result = runner.invoke(app, ["connectors", "list"], env=_WIDE)

    assert "prod" in _text(result)
    assert "slack" in _text(result)


def test_empty_list_points_at_create():
    _auth()
    w = _wrapper(list=[])
    with _patch(w):
        result = runner.invoke(app, ["connectors", "list"], env=_WIDE)

    assert "connectors create" in _text(result)


def test_status_flags_a_connector_with_no_active_resources():
    """The common "why is nothing syncing" case, named rather than left implicit."""
    _auth()
    w = _wrapper(get={"connector_id": "c1", "provider": "slack", "active_resource_count": 0})
    with _patch(w):
        result = runner.invoke(app, ["connectors", "status", "c1"], env=_WIDE)

    assert "will not sync anything" in _text(result)


def test_discover_shows_the_id_configure_expects():
    """Discovery returns `id`; configure takes `resource_id`. The ids must flow."""
    _auth()
    w = _wrapper(discover={"resources": [{"id": "companies", "resource_type": "attio_object", "name": "Companies"}]})
    with _patch(w):
        result = runner.invoke(app, ["connectors", "discover", "c1"], env=_WIDE)

    assert "companies" in _text(result)


def test_configure_parses_resource_id_and_type():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(
            app,
            ["connectors", "configure", "c1", "-r", "C123:channel", "-r", "C456", "--lookback-days", "60"],
            env=_WIDE,
        )

    assert result.exit_code == 0
    kwargs = w.connectors.configure.call_args.kwargs
    assert kwargs["resources"] == [
        {"resource_id": "C123", "resource_type": "channel"},
        {"resource_id": "C456"},
    ]
    assert kwargs["lookback_days"] == 60


def test_configure_requires_at_least_one_resource():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "configure", "c1"], env=_WIDE)

    assert result.exit_code == 1
    assert "Nothing was configured" in _text(result)
    w.connectors.configure.assert_not_called()


def test_sync_says_it_is_asynchronous():
    """A user who queries immediately and finds nothing has not hit a failure."""
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "sync", "c1"], env=_WIDE)

    assert result.exit_code == 0
    assert "asynchronous" in _text(result)


def test_delete_requires_confirmation():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "delete", "c1"], input="\n", env=_WIDE)

    assert result.exit_code != 0
    w.connectors.delete.assert_not_called()


def test_delete_proceeds_with_yes_and_notes_data_remains():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "delete", "c1", "--yes"], env=_WIDE)

    assert result.exit_code == 0
    w.connectors.delete.assert_called_once_with("c1")
    assert "remain" in _text(result)


def test_resource_remove_requires_confirmation():
    _auth()
    w = _wrapper()
    with _patch(w):
        result = runner.invoke(app, ["connectors", "resource", "remove", "c1", "r1"], input="\n", env=_WIDE)

    assert result.exit_code != 0
    w.connectors.remove_resource.assert_not_called()


def test_json_output_for_list_is_the_array():
    _auth()
    w = _wrapper(list=[{"connector_id": "c1", "provider": "slack"}])
    with _patch(w):
        result = runner.invoke(app, ["--output", "json", "connectors", "list"], env=_WIDE)

    assert json.loads(result.output) == [{"connector_id": "c1", "provider": "slack"}]
