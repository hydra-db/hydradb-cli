"""Tests for hydradb_cli.config module."""

import json

import pytest

import hydradb_cli.config
from hydradb_cli.config import (
    DEFAULT_BASE_URL,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_SUB_TENANT_ID,
    ENV_TENANT_ID,
    clear_config,
    get_api_key,
    get_base_url,
    get_full_config,
    get_sub_tenant_id,
    get_tenant_id,
    save_config,
)


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    """Use a temp config dir for all tests."""
    config_dir = tmp_path / ".hydradb"
    config_file = config_dir / "config.json"
    monkeypatch.setattr("hydradb_cli.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("hydradb_cli.config.CONFIG_FILE", config_file)
    # Clear canonical + deprecated-alias env vars so the developer's own shell
    # (which exports HYDRADB_TENANT_ID etc.) never leaks into tests.
    for var in (
        ENV_API_KEY,
        ENV_TENANT_ID,
        ENV_SUB_TENANT_ID,
        ENV_BASE_URL,
        "HYDRADB_TENANT_ID",
        "HYDRADB_SUB_TENANT_ID",
        "HYDRADB_API_URL",
        "HYDRA_DB_API_KEY",
        "HYDRA_DB_TENANT_ID",
        "HYDRA_DB_SUB_TENANT_ID",
        "HYDRA_DB_BASE_URL",
        "HYDRA_OPENCLAW_API_KEY",
        "HYDRA_OPENCLAW_TENANT_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    yield config_file


class TestSaveAndReadConfig:
    def test_save_and_read_api_key(self, clean_config):
        save_config(api_key="test-key-123")
        assert get_api_key() == "test-key-123"

    def test_save_and_read_tenant_id(self, clean_config):
        save_config(tenant_id="my-tenant")
        assert get_tenant_id() == "my-tenant"

    def test_save_and_read_sub_tenant_id(self, clean_config):
        save_config(sub_tenant_id="sub-1")
        assert get_sub_tenant_id() == "sub-1"

    def test_save_and_read_base_url(self, clean_config):
        save_config(base_url="https://custom.api.com/")
        assert get_base_url() == "https://custom.api.com"  # trailing slash stripped

    def test_save_multiple_values(self, clean_config):
        save_config(api_key="key1", tenant_id="t1")
        assert get_api_key() == "key1"
        assert get_tenant_id() == "t1"

    def test_save_preserves_existing(self, clean_config):
        save_config(api_key="key1")
        save_config(tenant_id="t1")
        assert get_api_key() == "key1"
        assert get_tenant_id() == "t1"

    def test_config_file_permissions(self, clean_config):
        save_config(api_key="secret")
        assert oct(clean_config.stat().st_mode)[-3:] == "600"


class TestScopeKeyAliasing:
    """Each scope is one slot, reachable under either spelling.

    ``get_database``/``get_collection`` read the canonical key first, so writing
    only the deprecated spelling onto a config that already carries the
    canonical one would leave the old value in force while ``config set``
    reported success.
    """

    def test_deprecated_key_overrides_existing_canonical(self, clean_config):
        save_config(database="prod")
        save_config(tenant_id="staging")
        assert get_tenant_id() == "staging"

    def test_canonical_key_overrides_existing_deprecated(self, clean_config):
        save_config(tenant_id="prod")
        save_config(database="staging")
        assert get_tenant_id() == "staging"

    def test_deprecated_collection_overrides_existing_canonical(self, clean_config):
        save_config(collection="col-a")
        save_config(sub_tenant_id="col-b")
        assert get_sub_tenant_id() == "col-b"

    def test_canonical_collection_overrides_existing_deprecated(self, clean_config):
        save_config(sub_tenant_id="col-a")
        save_config(collection="col-b")
        assert get_sub_tenant_id() == "col-b"

    def test_canonical_argument_wins_over_deprecated_in_one_call(self, clean_config):
        save_config(database="canonical", tenant_id="deprecated")
        assert get_tenant_id() == "canonical"
        save_config(collection="canonical-col", sub_tenant_id="deprecated-col")
        assert get_sub_tenant_id() == "canonical-col"

    def test_both_spellings_written_for_older_cli_versions(self, clean_config):
        """A config written here must stay readable by a CLI that only knows
        the deprecated keys."""
        save_config(database="prod", collection="col")
        stored = json.loads(clean_config.read_text())
        assert stored["database"] == stored["tenant_id"] == "prod"
        assert stored["collection"] == stored["sub_tenant_id"] == "col"

    def test_scope_write_leaves_other_values_alone(self, clean_config):
        save_config(api_key="key1", base_url="https://api.example.com")
        save_config(tenant_id="staging")
        assert get_api_key() == "key1"
        assert get_base_url() == "https://api.example.com"


class TestEnvVarOverride:
    def test_env_overrides_api_key(self, clean_config, monkeypatch):
        save_config(api_key="file-key")
        monkeypatch.setenv(ENV_API_KEY, "env-key")
        assert get_api_key() == "env-key"

    def test_env_overrides_tenant_id(self, clean_config, monkeypatch):
        save_config(tenant_id="file-tenant")
        monkeypatch.setenv(ENV_TENANT_ID, "env-tenant")
        assert get_tenant_id() == "env-tenant"

    def test_env_overrides_base_url(self, clean_config, monkeypatch):
        save_config(base_url="https://file.api.com")
        monkeypatch.setenv(ENV_BASE_URL, "https://env.api.com")
        assert get_base_url() == "https://env.api.com"


class TestDeprecatedEnvAliases:
    """`HYDRADB_API_URL` is the base-URL spelling this client's docs page shipped, so
    CONTRACT §1's per-client scoping rule makes it one of the CLI's legacy names."""

    @pytest.fixture(autouse=True)
    def _reset_warnings(self):
        hydradb_cli.config._warned_env_aliases.clear()
        yield

    def test_hydradb_api_url_is_honoured(self, clean_config, monkeypatch):
        monkeypatch.setenv("HYDRADB_API_URL", "https://legacy.api.com")
        assert get_base_url() == "https://legacy.api.com"

    def test_hydradb_api_url_warns_once(self, clean_config, monkeypatch, capsys):
        monkeypatch.setenv("HYDRADB_API_URL", "https://legacy.api.com")
        get_base_url()
        get_base_url()
        err = capsys.readouterr().err
        assert err.count("HYDRADB_API_URL is deprecated") == 1
        assert "HYDRADB_BASE_URL" in err

    def test_canonical_base_url_wins_and_is_silent(self, clean_config, monkeypatch, capsys):
        monkeypatch.setenv("HYDRADB_API_URL", "https://legacy.api.com")
        monkeypatch.setenv(ENV_BASE_URL, "https://canonical.api.com")
        assert get_base_url() == "https://canonical.api.com"
        assert capsys.readouterr().err == ""

    def test_hydra_db_base_url_still_honoured(self, clean_config, monkeypatch):
        monkeypatch.setenv("HYDRA_DB_BASE_URL", "https://old.api.com")
        assert get_base_url() == "https://old.api.com"

    def test_other_clients_prefixes_are_not_read(self, clean_config, monkeypatch):
        """§1: a client reads only the legacy prefixes it itself shipped."""
        monkeypatch.setenv("HYDRA_OPENCLAW_API_KEY", "openclaw-key")
        assert get_api_key() is None


class TestDefaults:
    def test_default_base_url(self, clean_config):
        assert get_base_url() == DEFAULT_BASE_URL

    def test_no_api_key_returns_none(self, clean_config):
        assert get_api_key() is None

    def test_no_tenant_id_returns_none(self, clean_config):
        assert get_tenant_id() is None

    def test_no_sub_tenant_returns_none(self, clean_config):
        assert get_sub_tenant_id() is None


class TestClearConfig:
    def test_clear_removes_file(self, clean_config):
        save_config(api_key="key")
        assert clean_config.exists()
        clear_config()
        assert not clean_config.exists()

    def test_clear_nonexistent_is_safe(self, clean_config):
        clear_config()  # Should not raise


class TestGetFullConfig:
    def test_full_config_structure(self, clean_config):
        save_config(api_key="k", tenant_id="t")
        cfg = get_full_config()
        assert cfg["api_key"] == "k"
        assert cfg["tenant_id"] == "t"
        assert cfg["base_url"] == DEFAULT_BASE_URL
        assert cfg["api_key_source"] == "file"
        assert cfg["tenant_id_source"] == "file"
        assert "config_file" in cfg

    def test_full_config_env_source(self, clean_config, monkeypatch):
        monkeypatch.setenv(ENV_API_KEY, "env-k")
        cfg = get_full_config()
        assert cfg["api_key_source"] == "env"

    def test_full_config_no_source(self, clean_config):
        cfg = get_full_config()
        assert cfg["api_key_source"] == "none"
        assert cfg["tenant_id_source"] == "none"


class TestCorruptConfig:
    def test_corrupt_json_returns_empty(self, clean_config):
        clean_config.parent.mkdir(parents=True, exist_ok=True)
        clean_config.write_text("not valid json{{{")
        assert get_api_key() is None
        assert get_tenant_id() is None
