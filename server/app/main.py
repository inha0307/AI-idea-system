import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (테이블 등록)
from app.ai import detector
from app.config import settings
from app.db import Base, engine
from app.routers import cracks, health, inspections, structures, weather

logging.basicConfig(level=logging.INFO)

settings.storage_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # 없는 테이블만 생성
    detector.load_model(settings.model_path, settings.DEVICE)  # 1번만 로드
    yield


app = FastAPI(title="CrackTwin API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (health, structures, inspections, cracks, weather):
    app.include_router(r.router)

app.mount("/files", StaticFiles(directory=settings.storage_dir), name="files")
