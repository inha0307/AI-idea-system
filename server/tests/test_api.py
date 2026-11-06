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
    "ADMIN_PASSWORD": "test-admin-pass",
    "AUTH_SECRET": "test-secret",
})

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Crack, Structure  # noqa: E402

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
            db.add(Structure(name="테스트 건물", address="강릉", model_url="/files/assets/structure.glb",
                             latitude=37.8014, longitude=128.9088))
            db.add(Structure(name="위치 없는 건물", address="?"))
            db.commit()
        # 대부분의 테스트는 관리자로 실행 (게스트 규칙은 test_auth.py)
        token = c.post("/auth/login", json={"password": "test-admin-pass"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {token}"
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
    assert body["type"] == "detect_result"
    assert body["inspectionId"] == insp_id
    assert body["captureId"] == "c-0001"
    crack = body["cracks"][0]
    assert crack["severity"] == "caution"
    # 균열별 위치: 가짜 박스 중심이 화면 가운데 근처라 hit에서 몇 cm 안
    assert all(abs(crack["pos"][k] - META["hit"][k]) < 10 for k in "xyz")
    assert isinstance(crack["trackId"], int)
    assert crack["bbox"] == [812, 400, 1104, 690]
    assert crack["imageUrl"] == f"/files/captures/{insp_id}/c-0001_orig.jpg"
    assert (crack["distanceCm"], crack["fovDeg"], crack["imageWidth"]) == (430.0, 90.0, 1920)

    # 저장된 이미지가 /files로 열림
    assert client.get(crack["overlayUrl"]).status_code == 200

    # 목록·상세·필터
    s = client.get("/structures").json()[0]
    assert s["crackCounts"] == {"safe": 0, "caution": 1, "danger": 0}
    assert s["lastInspectedAt"] is not None
    assert len(client.get("/structures/1/cracks?severity=caution").json()) == 1
    assert client.get("/structures/1/cracks?severity=danger").json() == []
    detail = client.get(f"/cracks/{crack['id']}").json()
    assert detail["widthMm"] == 1.8
    assert (detail["distanceCm"], detail["fovDeg"], detail["imageWidth"]) == (430.0, 90.0, 1920)
    listed = client.get("/structures/1/cracks").json()[0]
    assert listed["imageWidth"] == 1920


def test_capture_conditions_unknown_are_null(client):
    """촬영 조건 없이 저장된 균열은 null로 돌려줌."""
    insp_id = client.post("/inspections", json={"structureId": 1}).json()["id"]
    with SessionLocal() as db:
        c = Crack(inspection_id=insp_id, structure_id=1, pos_x=0, pos_y=0, pos_z=0,
                  length_mm=1, width_mm=1, area_mm2=1, severity="safe", confidence=0.5)
        db.add(c)
        db.commit()
        crack_id = c.id
    body = client.get(f"/cracks/{crack_id}").json()
    assert (body["distanceCm"], body["fovDeg"], body["imageWidth"]) == (None, None, None)


def test_ue5_style_multipart(client):
    """UE5 C++ 예제(docs 디지털트윈팀 2-6)가 직접 만드는 multipart 본문을 그대로 보냄."""
    insp_id = client.post("/inspections", json={"structureId": 1}).json()["id"]
    boundary = "----CrackTwinBoundary7d1f"
    meta = json.dumps({**META, "id": "c-ue5"}, separators=(",", ":"))
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"c-ue5.jpg\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + _jpg() + f"\r\n--{boundary}--\r\n".encode()
    r = client.post(
        f"/inspections/{insp_id}/detect", content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["captureId"] == "c-ue5"


def test_latest_inspection_and_inspection_filter(client):
    a = client.post("/inspections", json={"structureId": 1}).json()["id"]
    assert _detect(client, a, {**META, "id": "c-latest"}).status_code == 200
    b = client.post("/inspections", json={"structureId": 1}).json()["id"]  # 빈 점검 (균열 없음)

    s = client.get("/structures/1").json()
    latest = s["latestInspection"]
    assert latest["id"] == a  # 빈 점검 b는 건너뜀
    assert latest["crackCounts"] == {"safe": 0, "caution": 1, "danger": 0}
    total = s["crackCounts"]
    assert total["caution"] >= 1 and sum(total.values()) >= sum(latest["crackCounts"].values())
    assert client.get("/structures").json()[0]["latestInspection"]["id"] == a

    only_a = client.get(f"/structures/1/cracks?inspectionId={a}").json()
    assert len(only_a) == 1 and only_a[0]["inspectionId"] == a
    assert client.get(f"/structures/1/cracks?inspectionId={b}").json() == []
    assert client.get(f"/structures/1/cracks?inspectionId={a}&severity=danger").json() == []
    assert len(client.get(f"/structures/1/cracks?inspectionId={a}&severity=caution").json()) == 1


def test_latest_inspection_null_without_cracks(client):
    assert client.get("/structures/2").json()["latestInspection"] is None


def test_severity_thresholds(client):
    assert client.get("/severity-thresholds").json() == {"cautionMm": 0.3, "dangerMm": 1.0}


def test_image_width_from_uploaded_image(client):
    insp_id = client.post("/inspections", json={"structureId": 1}).json()["id"]
    ok, buf = cv2.imencode(".jpg", np.full((720, 1280, 3), 150, np.uint8))
    crack = _detect(client, insp_id, image=buf.tobytes()).json()["cracks"][0]
    assert crack["imageWidth"] == 1280


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
