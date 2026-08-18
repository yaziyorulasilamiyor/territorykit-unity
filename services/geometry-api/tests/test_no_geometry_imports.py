"""Static proof that request-serving code cannot import a geometry library (FAZ-3-PLAN.md §1.2,
decision Z19 — the structural half of the "no geometry at request time" guarantee).

Pure ``ast`` parsing — no ``import``, no execution — so the absence of ``shapely``/
``mapbox_earcut``/``topojson`` (and everything that imports them: ``build.py``, ``loader.py``,
``projection.py``, ``triangulate.py``, ``simplify.py``, ``encoding.py``, ``loss.py``,
``manifest_validation.py``) is a fact about the source text, not about what happened to run.

This is one half of the guarantee; ``test_no_geometry_at_startup.py`` is the other. A missing
import statement proves the module *cannot* reach the forbidden code directly, not that nothing
it calls does — the subprocess test observes the running application instead of reading its
source, which is what closes that gap.
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_AT_REQUEST_TIME = frozenset(
    {
        "shapely",
        "mapbox_earcut",
        "topojson",
        "geometry_api.build",
        "geometry_api.loader",
        "geometry_api.projection",
        "geometry_api.triangulate",
        "geometry_api.simplify",
        "geometry_api.encoding",
        "geometry_api.loss",
        "geometry_api.manifest_validation",
    }
)

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "geometry_api"

# Top-level modules that run while a request is being served. routes/**/*.py is added
# dynamically below (not hand-maintained) so a new route file is covered automatically.
_SERVING_MODULES = (
    "main.py",
    "deps.py",
    "registry.py",
    "revisions.py",
    "errors.py",
    "pagination.py",
    "metrics.py",
    "tkmb.py",
    "cache.py",
    "conditional.py",
    "config.py",
)


def _serving_files() -> list[Path]:
    files = [SRC_ROOT / name for name in _SERVING_MODULES]
    files += sorted((SRC_ROOT / "routes").rglob("*.py"))
    return files


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.level:  # a relative import — from . import X / from .Y import Z
                prefix = "geometry_api" + ("." + node.module if node.module else "")
                names.add(prefix)
            else:
                names.add(node.module)
    return names


def _is_forbidden(name: str) -> bool:
    return any(
        name == forbidden or name.startswith(forbidden + ".")
        for forbidden in FORBIDDEN_AT_REQUEST_TIME
    )


def _forbidden_hits(imported: set[str]) -> set[str]:
    return {name for name in imported if _is_forbidden(name)}


def test_the_serving_file_set_is_not_accidentally_empty() -> None:
    """Guards the guard: if _serving_files() ever returned too few files, the test below would
    pass for the wrong reason — nothing left to check."""
    files = _serving_files()
    assert len(files) >= len(_SERVING_MODULES) + 5, files


def test_no_forbidden_imports_in_request_serving_code() -> None:
    violations: dict[str, set[str]] = {}
    for path in _serving_files():
        assert path.exists(), f"expected request-serving module is missing: {path}"
        hit = _forbidden_hits(_imported_module_names(path))
        if hit:
            violations[str(path.relative_to(SRC_ROOT))] = hit
    assert not violations, (
        f"request-serving module(s) import geometry code, violating the phase 3 'no geometry at "
        f"request time' rule: {violations}"
    )
