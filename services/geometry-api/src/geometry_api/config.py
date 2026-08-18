from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_comma_list(value: object) -> object:
    """Accept a plain comma-separated env var for a list field.

    pydantic-settings expects JSON for complex types by default, which makes
    ``GEOMETRY_API_CORS_ALLOW_ORIGINS=https://a,https://b`` in a ``.env`` file fail to parse. A
    comma-separated string is what an operator actually writes, so list-typed settings accept it
    explicitly via a ``mode="before"`` validator instead.
    """
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    dataset_dir: str = "data/datasets"
    """Raw source datasets (GeoJSON / dataset.json). Read only by build scripts, never by the API
    process — see docs/phases/FAZ-3-PLAN.md §2."""

    artifacts_dir: str = "data/artifacts"
    """Published, revisioned builds. Written only by scripts/publish_dataset.py; the API process
    only ever reads from here."""

    cache_dir: str = "data/cache"
    """API-writable runtime scratch space: the content-addressed batch cache and lease files
    (FAZ-3-PLAN.md §2, §3.4). Never touched by publish_dataset.py except to clean up a pruned
    revision's cache/lease subdirectories."""

    # ---- revision retention (scripts/publish_dataset.py) --------------------------------------
    revision_retain_count: int = 3
    """How many revisions of one dataset to keep on disk, counting the current one.

    Dataset-wide, not per level — one revision already spans high/medium/low (FAZ-3-PLAN.md §3.3).
    A policy choice, not a measurement: 3 was picked so a client that fetched a mesh URL just
    before a republish still has a retention window to finish using it. The CLI flag
    ``--keep`` on publish_dataset.py overrides this per invocation; both must be >= 1.
    """

    # ---- CORS -----------------------------------------------------------------------------------
    cors_allow_origins: list[str] = []
    """Closed by default — no origin allowed until explicitly configured (FAZ-3-PLAN.md §13.2)."""

    # ---- territories / viewport pagination ------------------------------------------------------
    territories_page_size_default: int = 50
    territories_page_size_max: int = 500
    territories_scan_cap: int = 5000
    """Upper bound on how many manifest entries a single page's filter scan may examine before
    returning a (possibly short) page with scanTruncated: true, rather than scanning unbounded
    (FAZ-3-PLAN.md §4.3)."""

    # ---- mesh batch (TKMB) ------------------------------------------------------------------
    batch_max_territories: int = 200
    batch_cache_max_bytes: int = 512 * 1024 * 1024

    # ---- readiness ----------------------------------------------------------------------------
    required_dataset_ids: list[str] | None = None
    """Datasets /ready must find healthy. None means "whatever artifacts_dir contains at startup"
    (FAZ-3-PLAN.md §13.1); set explicitly to make a missing dataset a readiness failure rather
    than silent absence."""

    # ---- lease-protected pruning ----------------------------------------------------------------
    lease_stale_after_seconds: int = 300
    """A lease file older than this is treated as abandoned (a crashed request) and ignored by
    publish_dataset.py's pruning step, rather than blocking pruning forever (FAZ-3-PLAN.md §3.4)."""

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _parse_cors_allow_origins(cls, value: object) -> object:
        return _split_comma_list(value)

    @field_validator("required_dataset_ids", mode="before")
    @classmethod
    def _parse_required_dataset_ids(cls, value: object) -> object:
        if value in (None, ""):
            return None
        return _split_comma_list(value)

    model_config = SettingsConfigDict(
        env_prefix="GEOMETRY_API_", env_file=("../../.env", ".env"), env_file_encoding="utf-8"
    )


settings = Settings()
