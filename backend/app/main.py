from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import close_database, init_database
from app.services.ai import get_embedding_service


settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    if not settings.demo_mode:
        embeddings = get_embedding_service(settings)
        await embeddings.encode_e5("LostLink AI model warmup")
        await embeddings.encode_siglip_text("a lost item")
    yield
    await close_database()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="LINE-first multimodal lost-and-found matching API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


@app.get("/")
async def root() -> dict[str, str | bool]:
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "demo_mode": settings.demo_mode,
        "matching_mode": "demo" if settings.demo_mode else "e5+siglip2",
    }


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "demo_mode": settings.demo_mode,
        "matching_mode": "demo" if settings.demo_mode else "e5+siglip2",
    }
