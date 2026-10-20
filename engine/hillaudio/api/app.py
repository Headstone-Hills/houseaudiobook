"""FastAPI application factory.

The engine binds to localhost only (see `main`): the UI, whether a browser tab or
the native shell, is the only intended client, and no audio or text ever leaves
the machine.
"""

from __future__ import annotations

from fastapi import FastAPI

from hillaudio import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="HillAudiobook engine", version=__version__)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("hillaudio.api.app:app", host="127.0.0.1", port=8765, reload=True)


if __name__ == "__main__":
    main()
