import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import auth
from app import models  # noqa: F401  (테이블 등록)
from app.ai import detector
from app.config import settings
from app.db import Base, engine
from app.routers import auth as auth_router
from app.routers import cracks, health, inspections, structures, weather

logging.basicConfig(level=logging.INFO)

settings.storage_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # 없는 테이블만 생성
    detector.load_model(settings.model_path, settings.DEVICE)  # 1번만 로드
    yield


app = FastAPI(title="CrackTwin API", version="0.1.0", lifespan=lifespan)

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


# 외부(터널) 접속은 조회만. 각 API의 require_admin/require_local과 별도로 한 번 더 막는 안전장치
@app.middleware("http")
async def guest_read_only(request: Request, call_next):
    if auth.via_tunnel(request):
        if request.url.path.startswith(DOC_PATHS):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"detail": "외부 접속(게스트)은 조회만 할 수 있습니다"}, status_code=403)
    return await call_next(request)


# CORS는 맨 바깥에 두어 403 응답에도 CORS 헤더가 붙게 함 (나중에 추가한 미들웨어가 바깥)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (health, auth_router, structures, inspections, cracks, weather):
    app.include_router(r.router)

app.mount("/files", StaticFiles(directory=settings.storage_dir), name="files")
