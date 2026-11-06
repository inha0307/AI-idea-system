"""관리자 / 게스트 역할.

- 게스트: 조회(GET)만. QR로 Vercel 웹 → Cloudflare 터널로 들어온 외부 사람은 항상 게스트.
- 관리자: 시연 PC 로컬 웹에서 비밀번호로 로그인 → 토큰(Authorization: Bearer)으로 쓰기 가능.
- UE5 사진 업로드(detect)는 토큰 없이 로컬 네트워크에서만 허용.

터널 여부: Cloudflare가 터널을 거친 요청에 붙이는 Cf-Connecting-Ip 헤더로 판단한다.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, Request

from app.config import settings

TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")

# AUTH_SECRET이 비어 있으면 실행할 때마다 새로 만듦 (재시작하면 다시 로그인)
_secret = (settings.AUTH_SECRET or secrets.token_urlsafe(32)).encode()


def via_tunnel(request: Request) -> bool:
    return any(h in request.headers for h in TUNNEL_HEADERS)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(hours: float | None = None) -> tuple[str, int]:
    exp = int(time.time() + 3600 * (hours or settings.ADMIN_TOKEN_HOURS))
    body = _b64(json.dumps({"role": "admin", "exp": exp}).encode())
    sig = _b64(hmac.new(_secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", exp


def token_valid(token: str) -> bool:
    try:
        body, sig = token.split(".")
        want = _b64(hmac.new(_secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return False
        data = json.loads(_unb64(body))
        return data.get("role") == "admin" and data.get("exp", 0) > time.time()
    except Exception:
        return False


def bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def role_of(request: Request) -> str:
    if via_tunnel(request):
        return "guest"
    t = bearer(request)
    return "admin" if t and token_valid(t) else "guest"


def check_password(password: str) -> bool:
    if not settings.ADMIN_PASSWORD:
        return False
    return hmac.compare_digest(password.encode(), settings.ADMIN_PASSWORD.encode())


# ---------- FastAPI 의존성 ----------

def require_admin(request: Request) -> None:
    if via_tunnel(request):
        raise HTTPException(403, "외부 접속(게스트)은 조회만 할 수 있습니다")
    t = bearer(request)
    if not t or not token_valid(t):
        raise HTTPException(401, "관리자 로그인이 필요합니다")


def require_local(request: Request) -> None:
    if via_tunnel(request):
        raise HTTPException(403, "외부 접속(게스트)은 조회만 할 수 있습니다")
