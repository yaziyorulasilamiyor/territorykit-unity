"""Runtime proof that neither starting the app nor serving real requests through it loads a
geometry library (FAZ-3-PLAN.md §1.2, decision Z19 — the observational half; see
``test_no_geometry_imports.py`` for the structural half).

Runs in a **clean subprocess** rather than deleting entries from ``sys.modules`` in-process: this
test's own process has almost certainly already imported ``shapely`` (other test modules do), so
checking ``sys.modules`` here would either always fail or require unreliable surgery on an
interpreter state this test does not own. A fresh ``python -c`` process starts with nothing
imported, so what ends up in its ``sys.modules`` is exactly what the code under test loaded.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
GEOMETRY_API_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = TESTS_DIR.parents[2] / "scripts"

_FORBIDDEN = ("shapely", "mapbox_earcut", "topojson")

_IMPORT_ONLY_SCRIPT = """
import sys
import geometry_api.main  # noqa: F401

forbidden = ("shapely", "mapbox_earcut", "topojson")
loaded = [name for name in forbidden if name in sys.modules]
assert not loaded, f"geometry libraries loaded merely by importing the app: {loaded}"
print("OK")
"""

# A static string, not an f-string — the tmp paths travel through environment variables instead
# of string interpolation, so this script's own f-strings (built at *subprocess* run time) need
# no brace-escaping here.
_SERVE_REQUESTS_SCRIPT = """
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["SCRIPTS_DIR"])
sys.path.insert(0, os.environ["TESTS_DIR"])

import publish_dataset
from publish_fixtures import write_healthy_build

build_dir = Path(os.environ["BUILD_DIR"])
artifacts_dir = Path(os.environ["ARTIFACTS_DIR"])
cache_dir = Path(os.environ["CACHE_DIR"])
write_healthy_build(build_dir)
publish_dataset.publish(build_dir, "fixture", artifacts_dir, cache_dir=cache_dir)

from geometry_api.config import settings

settings.artifacts_dir = str(artifacts_dir)
settings.cache_dir = str(cache_dir)

from fastapi.testclient import TestClient

from geometry_api.main import app

with TestClient(app) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/v1/datasets").status_code == 200
    dataset_response = client.get("/v1/datasets/fixture")
    assert dataset_response.status_code == 200, dataset_response.text
    revision_id = dataset_response.json()["revisionId"]
    territories_url = "/v1/datasets/fixture/territories?lod=high"
    assert client.get(territories_url).status_code == 200
    viewport_url = "/v1/datasets/fixture/viewport?lod=high&bbox=-10,-10,10,10"
    assert client.get(viewport_url).status_code == 200

    mesh_url = f"/v1/datasets/fixture/revisions/{revision_id}/mesh/T1?lod=high"
    mesh_response = client.get(mesh_url, headers={"Accept-Encoding": "identity"})
    assert mesh_response.status_code == 200, mesh_response.text

    batch_response = client.post(
        f"/v1/datasets/fixture/revisions/{revision_id}/mesh/batch",
        json={"territoryIds": ["T1"], "lod": "high"},
    )
    assert batch_response.status_code == 200, batch_response.text

forbidden = ("shapely", "mapbox_earcut", "topojson")
loaded = [name for name in forbidden if name in sys.modules]
assert not loaded, f"geometry libraries loaded while serving requests: {loaded}"
print("OK")
"""


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=GEOMETRY_API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_importing_the_app_does_not_load_geometry_libraries() -> None:
    result = _run(_IMPORT_ONLY_SCRIPT, dict(os.environ))
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout


def test_serving_real_requests_does_not_load_geometry_libraries(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["SCRIPTS_DIR"] = str(SCRIPTS_DIR)
    env["TESTS_DIR"] = str(TESTS_DIR)
    env["BUILD_DIR"] = str(tmp_path / "build")
    env["ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
    env["CACHE_DIR"] = str(tmp_path / "cache")

    result = _run(_SERVE_REQUESTS_SCRIPT, env)

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout
