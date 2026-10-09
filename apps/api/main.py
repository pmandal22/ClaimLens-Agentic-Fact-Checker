"""FastAPI app factory."""

from fastapi import FastAPI

from apps.api.routes import checks


def create_app() -> FastAPI:
    app = FastAPI(title="ClaimLens")
    app.include_router(checks.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
