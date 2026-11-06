"""관리자 / 게스트 역할. 외부 사람은 Cloudflare 터널(Cf-Connecting-Ip 헤더)로만 들어온다."""

import json

from app import auth
from tests.test_api import META, _jpg, client  # noqa: F401

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7"}


def no_auth(c):
    return {k: v for k, v in c.headers.items() if k.lower() != "authorization"}


def test_login(client):  # noqa: F811
    assert client.post("/auth/login", json={"password": "wrong"}).status_code == 401
    r = client.post("/auth/login", json={"password": "test-admin-pass"})
    assert r.status_code == 200 and r.json()["role"] == "admin" and auth.token_valid(r.json()["token"])
    # 터널(외부)에서는 비밀번호가 맞아도 로그인 불가
    assert client.post("/auth/login", json={"password": "test-admin-pass"}, headers=TUNNEL).status_code == 403


def test_me(client):  # noqa: F811
    assert client.get("/auth/me").json() == {"role": "admin", "via": "local"}
    assert client.get("/auth/me", headers=TUNNEL).json() == {"role": "guest", "via": "tunnel"}
    import httpx
    anon = httpx.Client(transport=client._transport, base_url=str(client.base_url))
    assert anon.get("/auth/me").json() == {"role": "guest", "via": "local"}


def test_guest_can_read_everything(client):  # noqa: F811
    for path in ["/health", "/structures", "/structures/1", "/structures/1/cracks",
                 "/structures/1/cracks?latest=true", "/severity-thresholds"]:
        assert client.get(path, headers={**TUNNEL, "Authorization": ""}).status_code == 200, path
    crack = client.get("/structures/1/cracks", headers=TUNNEL).json()
    if crack:
        assert client.get(f"/cracks/{crack[0]['id']}", headers=TUNNEL).status_code == 200
        assert client.get(f"/cracks/{crack[0]['id']}/history", headers=TUNNEL).status_code == 200
        assert client.get(crack[0]["imageUrl"], headers=TUNNEL).status_code == 200


def test_guest_cannot_write_even_with_token(client):  # noqa: F811
    # 관리자 토큰이 붙어 있어도 터널로 오면 거절
    assert client.post("/inspections", json={"structureId": 1}, headers=TUNNEL).status_code == 403
    insp = client.post("/inspections", json={"structureId": 1}).json()["id"]
    r = client.post(f"/inspections/{insp}/detect", headers=TUNNEL,
                    files={"image": ("c.jpg", _jpg(), "image/jpeg")}, data={"meta": json.dumps(META)})
    assert r.status_code == 403
    assert client.delete("/structures/1", headers=TUNNEL).status_code == 403


def test_docs_hidden_from_tunnel(client):  # noqa: F811
    assert client.get("/docs").status_code == 200
    assert client.get("/docs", headers=TUNNEL).status_code == 404
    assert client.get("/openapi.json", headers=TUNNEL).status_code == 404


def test_local_write_needs_admin_token_except_ue5_upload(client):  # noqa: F811
    headers = no_auth(client)
    import httpx
    anon = httpx.Client(transport=client._transport, base_url=str(client.base_url), headers=headers)
    assert anon.post("/inspections", json={"structureId": 1}).status_code == 401
    bad = anon.post("/inspections", json={"structureId": 1}, headers={"Authorization": "Bearer x.y"})
    assert bad.status_code == 401
    # UE5 업로드는 토큰 없이 로컬에서 허용
    insp = client.post("/inspections", json={"structureId": 1}).json()["id"]
    r = anon.post(f"/inspections/{insp}/detect",
                  files={"image": ("c.jpg", _jpg(), "image/jpeg")},
                  data={"meta": json.dumps({**META, "id": "c-ue5-noauth"})})
    assert r.status_code == 200


def test_expired_or_forged_token():
    token, _ = auth.make_token(hours=-1)
    assert not auth.token_valid(token)
    good, _ = auth.make_token()
    body, sig = good.split(".")
    assert not auth.token_valid(body + "." + sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB"))
