import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from app import auth
from app.config import settings
from app.schemas import CamelModel

router = APIRouter(tags=["auth"])


class LoginIn(CamelModel):
    password: str


class LoginOut(CamelModel):
    token: str
    role: Literal["admin"] = "admin"
    expires_at: datetime


class MeOut(CamelModel):
    role: Literal["admin", "guest"]
    via: Literal["tunnel", "local"]


@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request):
    auth.require_local(request)  # 외부(터널)에서는 로그인 불가
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(503, "서버에 관리자 비밀번호(ADMIN_PASSWORD)가 설정되지 않았습니다")
    if not auth.check_password(body.password):
        await asyncio.sleep(0.5)  # 무작위 대입을 느리게
        raise HTTPException(401, "비밀번호가 틀렸습니다")
    token, exp = auth.make_token()
    return LoginOut(token=token, expires_at=datetime.fromtimestamp(exp, timezone.utc))


@router.get("/auth/me", response_model=MeOut)
def me(request: Request):
    return MeOut(role=auth.role_of(request), via="tunnel" if auth.via_tunnel(request) else "local")
