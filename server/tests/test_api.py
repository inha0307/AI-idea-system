"""API 흐름 테스트. Docker 없이 SQLite 임시 DB로 실행합니다.

실행: python -m pytest tests -q
"""

import json
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp())
os.environ.update({
    "DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "STORAGE_DIR": str(_tmp / "storage"),
    "DETECTOR": "fake",
    "SEVERITY_CAUTION_MM": "0.3",
    "SEVERITY_DANGER_MM": "1.0",
})

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Structure  # noqa: E402

META = {
    "type": "capture_result",
    "id": "c-0001",
    "structureId": 1,
    "drone": {"x": 820.0, "y": -150.0, "z": 400.0, "yaw": 90.0, "pitch": -5.0},
    "hit": {"x": 1250.0, "y": -150.0, "z": 380.0},
    "hitOk": True,
    "distanceCm": 430.0,
    "fovDeg": 90.0,
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            db.add(Structure(name="테스트 건물", address="춘천", model_url="/files/assets/structure.glb"))
            db.commit()
        yield c


def _jpg() -> bytes:
    ok, buf = cv2.imencode(".jpg", np.full((1080, 1920, 3), 150, np.uint8))
    return buf.tobytes()


def _detect(client, inspection_id, meta=META, image=None):
    return client.post(
        f"/inspections/{inspection_id}/detect",
        files={"image": ("cap.jpg", image or _jpg(), "image/jpeg")},
        data={"meta": json.dumps(meta)},
    )


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "db": True, "detector": "fake"}


def test_full_flow(client):
    r = client.post("/inspections", json={"structureId": 1})
    assert r.status_code == 201
    insp_id = r.json()["id"]

    r = _detect(client, insp_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inspectionId"] == insp_id
    crack = body["cracks"][0]
    assert crack["severity"] == "caution"
    assert crack["pos"] == META["hit"]
    assert crack["bbox"] == [812, 400, 1104, 690]
    assert crack["imageUrl"] == f"/files/captures/{insp_id}/c-0001_orig.jpg"

    # 저장된 이미지가 /files로 열림
    assert client.get(crack["overlayUrl"]).status_code == 200

    # 목록·상세·필터
    s = client.get("/structures").json()[0]
    assert s["crackCounts"] == {"safe": 0, "caution": 1, "danger": 0}
    assert s["lastInspectedAt"] is not None
    assert len(client.get("/structures/1/cracks?severity=caution").json()) == 1
    assert client.get("/structures/1/cracks?severity=danger").json() == []
    assert client.get(f"/cracks/{crack['id']}").json()["widthMm"] == 1.8


def test_errors(client):
    insp_id = client.post("/inspections", json={"structureId": 1}).json()["id"]
    assert client.post("/inspections", json={"structureId": 99}).status_code == 404
    assert _detect(client, 999).status_code == 404
    assert _detect(client, insp_id, {**META, "hitOk": False}).status_code == 422
    assert _detect(client, insp_id, {**META, "structureId": 2}).status_code == 422
    assert _detect(client, insp_id, {**META, "id": "../../evil"}).status_code == 422
    assert _detect(client, insp_id, image=b"not an image").status_code == 422
    assert client.get("/structures/1/cracks?severity=bad").status_code == 422
    assert client.get("/cracks/999").status_code == 404
