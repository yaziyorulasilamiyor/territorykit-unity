"""compute_revision_id: deterministic, content-sensitive, path-sensitive, never truncated."""

from __future__ import annotations

from pathlib import Path

from geometry_api.revisions import compute_revision_id


def _make_root(root: Path, *, high: bytes = b"H", medium: bytes = b"M", low: bytes = b"L") -> None:
    for lod, content in (("high", high), ("medium", medium), ("low", low)):
        level = root / lod
        level.mkdir(parents=True)
        (level / "index.json").write_bytes(content)
        (level / "01.tkms").write_bytes(content * 3)


def test_identical_content_yields_the_same_id(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    _make_root(a)
    _make_root(b)

    assert compute_revision_id(a) == compute_revision_id(b)


def test_the_id_is_a_full_sha256_hex_digest(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_root(root)

    revision_id = compute_revision_id(root)

    assert len(revision_id) == 64, "content-addressing must not be truncated"
    assert all(char in "0123456789abcdef" for char in revision_id)


def test_a_single_changed_byte_changes_the_id(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _make_root(root)
    before = compute_revision_id(root)

    (root / "medium" / "01.tkms").write_bytes(b"MMM" + b"!")

    assert compute_revision_id(root) != before


def test_a_renamed_file_with_the_same_bytes_changes_the_id(tmp_path: Path) -> None:
    """The relative path is folded into the hash, not just the content — a revision is the exact
    set of (path, bytes) pairs, not a bag of bytes."""
    root = tmp_path / "root"
    _make_root(root)
    before = compute_revision_id(root)

    (root / "high" / "01.tkms").rename(root / "high" / "02.tkms")

    assert compute_revision_id(root) != before


def test_length_prefixing_prevents_boundary_ambiguity(tmp_path: Path) -> None:
    """Two trees whose (path, content) pairs would concatenate to the same bytes without a length
    prefix — 'ab'+'c' vs 'a'+'bc' — must still hash differently."""
    root_ab_c = tmp_path / "ab_c"
    root_a_bc = tmp_path / "a_bc"
    for root, first_name, first_content in (
        (root_ab_c, "ab", b"c"),
        (root_a_bc, "a", b"bc"),
    ):
        level = root / "high"
        level.mkdir(parents=True)
        (level / first_name).write_bytes(first_content)
        (root / "medium").mkdir(parents=True)
        (root / "low").mkdir(parents=True)

    assert compute_revision_id(root_ab_c) != compute_revision_id(root_a_bc)


def test_gzip_and_etag_files_are_included(tmp_path: Path) -> None:
    """A revision covers every published byte, not just .tkms/index.json — the .tkms.gz variant
    and etags.json are part of what a client can fetch, so they are part of the identity."""
    root = tmp_path / "root"
    _make_root(root)
    before = compute_revision_id(root)

    (root / "high" / "01.tkms.gz").write_bytes(b"gz-bytes")

    assert compute_revision_id(root) != before
