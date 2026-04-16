from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from .api.routes import ContainerProtocol, build_router
from .observability import configure_logging
from .runtime import create_runtime_container


def create_app(container: Optional[ContainerProtocol] = None) -> FastAPI:
    configure_logging()
    runtime_container = container if container is not None else create_runtime_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = runtime_container
        startup = getattr(runtime_container, "startup", None)
        if callable(startup):
            await startup()
        try:
            yield
        finally:
            shutdown = getattr(runtime_container, "shutdown", None)
            if callable(shutdown):
                await shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.container = runtime_container
    app.include_router(build_router(container=runtime_container))
    return app
