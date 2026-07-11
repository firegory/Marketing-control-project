"""FastAPI application factory for the local UI."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app() -> FastAPI:
    """Create the loopback-only application's HTTP interface."""
    app = FastAPI(title="Marketing Control", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        """Render the initial local application shell."""
        return _templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report that the local process can serve requests."""
        return {"status": "ok"}

    return app
