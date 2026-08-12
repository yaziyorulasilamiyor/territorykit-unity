from fastapi import FastAPI

from geometry_api import __version__

app = FastAPI(title="TerritoryKit Geometry API", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
