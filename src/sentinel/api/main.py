from fastapi import FastAPI

from sentinel import __version__

app = FastAPI(title="Sentinel", version=__version__)


@app.get("/health/live")
def live() -> dict[str, str]:
    """The API process is running."""
    return {"status": "ok", "version": __version__}