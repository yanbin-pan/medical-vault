"""Runtime configuration.

Every setting has a default that works for local development, so `medvault`
starts with no environment at all. Production overrides come from the
environment (and, in the cluster, from a SOPS-encrypted Secret).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDVAULT_", env_file=".env", extra="ignore")

    # --- The canonical store -------------------------------------------------
    # In the cluster this is a PersistentVolumeClaim on the `ssd` StorageClass,
    # which is NFS-backed and included in the nightly backup. It must never
    # point at ephemeral or SD-card storage: this directory *is* the data.
    vault_path: Path = Path("./vault")

    # --- The derived index ---------------------------------------------------
    # SQLite, because this database is a cache and nothing more. At the scale
    # this application actually runs at -- a family's results over decades is
    # tens of thousands of rows, single-digit megabytes -- a server process
    # would buy concurrency the deployment cannot use anyway: the vault volume
    # is ReadWriteOnce, so there is exactly one writer by construction.
    #
    # In the cluster this points at an emptyDir, not at the SSD. Losing it is a
    # non-event by design: `medvault reindex` rebuilds it from the vault, and
    # the pod does exactly that on every start. Keeping it off NFS also sidesteps
    # SQLite's well-known locking problems there.
    #
    # Any SQLAlchemy URL works. PostgreSQL needs the `postgres` extra installed.
    database_url: str = "sqlite:///./medvault.db"

    # --- Extraction ----------------------------------------------------------
    anthropic_api_key: str = ""
    extraction_model: str = "claude-opus-5"
    extraction_max_tokens: int = 16000
    # Uploaded phone photographs are large; downscaling before upload cuts cost
    # and latency without measurably hurting reading accuracy.
    max_image_edge_px: int = 1568

    # --- Authentication ------------------------------------------------------
    # The cluster puts Cloudflare Access in front of this service, which strips
    # any client-supplied copy of the header below and sets its own. Trusting it
    # is only safe because nothing can reach the pod except through the tunnel.
    # Off by default so a misconfigured deployment fails closed.
    trust_access_header: bool = False
    access_email_header: str = "cf-access-authenticated-user-email"
    # Used for local development and for API tokens.
    dev_user_email: str = ""

    cors_origins: str = "http://localhost:5173"

    # Where the built single-page app lives. Empty in development, where Vite
    # serves it on its own port and proxies /api here.
    web_root: Path | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook: forget the memoised settings so env changes take effect."""
    global _settings
    _settings = None
