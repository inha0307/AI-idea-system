"""같은 균열 묶기: 균열별 위치 계산(geometry) + 트랙 배정(tracking) + API."""

import json
from datetime import datetime, timezone

import pytest

from app import geometry, tracking
from app.db import SessionLocal
from app.models import Structure
from tests.test_api import _jpg, client  # noqa: F401

W, H = 1920, 1080


def meta(hit=(1250.0, -150.0, 380.0), yaw=0.0, pitch=0.0, dist=100.0, fov=90.0, normal=None, **extra):
    m = {"drone": {"x": 0, "y": 0, "z": 0, "yaw": yaw, "pitch": pitch},
         "hit": {"x": hit[0], "y": hit[1], "z": hit[2]}, "distanceCm": dist, "fovDeg": fov}
    if normal:
        m["hitNormal"] = {"x": normal[0], "y": normal[1], "z": normal[2]}
    m.update(extra)
    return m


# ---------- geometry ----------

def test_center_pixel_is_hit():
    p = geometry.pixel_to_wall(meta(yaw=37, pitch=-12), W / 2, H / 2, W, H)
    assert p == pytest.approx((1250.0, -150.0, 380.0), abs=1e-6)


def test_pixel_offsets_follow_camera_axes():
    # 화각 90°, 거리 100 cm → 가로 960 px = 100 cm. 192 px 오른쪽 = 20 cm
    right = geometry.pixel_to_wall(meta(yaw=0), W / 2 + 192, H / 2, W, H)
    assert right == pytest.approx((1250.0, -130.0, 380.0), abs=1e-6)  # yaw 0: 오른쪽 = +Y
    up = geometry.pixel_to_wall(meta(yaw=0), W / 2, H / 2 - 96, W, H)
    assert up == pytest.approx((1250.0, -150.0, 390.0), abs=1e-6)  # 화면 위쪽 = +Z
    turned = geometry.pixel_to_wall(meta(yaw=180), W / 2 + 192, H / 2, W, H)
    assert turned[1] == pytest.approx(-170.0)  # yaw 180: 오른쪽 = −Y


def test_hit_normal_keeps_point_on_oblique_wall():
    # 벽은 x = 1250 평면 (법선 −X). 드론은 30° 비스듬히 바라봄
    m = meta(yaw=30, normal=(-1.0, 0.0, 0.0))
    for px, py in [(100, 100), (1800, 900), (960, 540)]:
        assert geometry.pixel_to_wall(m, px, py, W, H)[0] == pytest.approx(1250.0, abs=1e-6)
    # hitNormal 없이 정면 가정이면 비스듬한 벽에서 조금 어긋남
    frontal = geometry.pixel_to_wall(meta(yaw=30), 1800, 540, W, H)
    assert abs(frontal[0] - 1250.0) > 1


# ---------- tracking ----------

@pytest.fixture()
def structure_id(client):  # noqa: F811
    with SessionLocal() as db:
        s = Structure(name="트랙 테스트")
        db.add(s)
        db.commit()
        return s.id


def _assign(sid, items):
    with SessionLocal() as db:
        tracks = tracking.assign_tracks(db, sid, items, datetime.now(timezone.utc))
        db.commit()
        return [t.id for t in tracks]


def test_nearby_crack_joins_existing_track(structure_id):
    [t1] = _assign(structure_id, [((1250, -150, 380), 400.0)])
    [t2] = _assign(structure_id, [((1250, -140, 372), 420.0)])  # 13 cm 떨어짐
    [t3] = _assign(structure_id, [((1250, 200, 380), 400.0)])  # 350 cm 떨어짐
    assert t1 == t2 and t3 != t1


def test_two_cracks_in_one_photo_get_different_tracks(structure_id):
    a, b = _assign(structure_id, [((1250, 0, 300), 300.0), ((1250, 20, 300), 300.0)])
    assert a != b
    # 다음 사진에서 두 균열이 다시 찍히면 각자 가까운 트랙으로
    a2, b2 = _assign(structure_id, [((1250, 22, 301), 300.0), ((1250, -1, 299), 300.0)])
    assert (a2, b2) == (b, a)


def test_very_different_length_is_another_crack(structure_id):
    [t1] = _assign(structure_id, [((1250, 500, 500), 100.0)])
    [t2] = _assign(structure_id, [((1250, 505, 500), 450.0)])  # 4.5배 길이
    assert t1 != t2


# ---------- API ----------

def _detect(client, insp_id, cap_id, sid, hit, drone_x=1150.0):  # noqa: F811
    m = {"type": "capture_result", "id": cap_id, "structureId": sid,
         "drone": {"x": drone_x, "y": hit[1], "z": hit[2], "yaw": 0.0, "pitch": 0.0},
         "hit": {"x": hit[0], "y": hit[1], "z": hit[2]}, "hitOk": True,
         "distanceCm": hit[0] - drone_x, "fovDeg": 90.0}
    r = client.post(f"/inspections/{insp_id}/detect",
                    files={"image": ("c.jpg", _jpg(), "image/jpeg")}, data={"meta": json.dumps(m)})
    assert r.status_code == 200, r.text
    return r.json()["cracks"][0]


def test_api_same_place_twice_is_one_crack(client, structure_id):  # noqa: F811
    sid = structure_id
    insp = client.post("/inspections", json={"structureId": sid}).json()["id"]
    a = _detect(client, insp, "c-a", sid, (1250.0, -150.0, 380.0))
    b = _detect(client, insp, "c-b", sid, (1250.0, -140.0, 384.0), drone_x=1120.0)  # 조금 옮겨서 다시
    c = _detect(client, insp, "c-c", sid, (1250.0, 400.0, 380.0))  # 다른 곳
    assert a["trackId"] == b["trackId"] != c["trackId"]

    s = client.get(f"/structures/{sid}").json()
    assert s["crackCounts"] == {"safe": 0, "caution": 2, "danger": 0}  # 관측 3건, 균열 2개
    assert s["latestInspection"]["crackCounts"]["caution"] == 2

    assert len(client.get(f"/structures/{sid}/cracks").json()) == 3
    latest = client.get(f"/structures/{sid}/cracks?latest=true").json()
    assert {x["id"] for x in latest} == {b["id"], c["id"]}  # 균열마다 가장 최근 관측

    hist = client.get(f"/cracks/{b['id']}/history").json()
    assert [x["id"] for x in hist] == [a["id"], b["id"]]  # 오래된 순
    assert client.get("/cracks/999999/history").status_code == 404


def test_api_hit_normal_accepted(client, structure_id):  # noqa: F811
    sid = structure_id
    insp = client.post("/inspections", json={"structureId": sid}).json()["id"]
    m = {"type": "capture_result", "id": "c-n", "structureId": sid,
         "drone": {"x": 1150.0, "y": -150.0, "z": 380.0, "yaw": 0.0, "pitch": 0.0},
         "hit": {"x": 1250.0, "y": -150.0, "z": 380.0}, "hitNormal": {"x": -1.0, "y": 0.0, "z": 0.0},
         "hitOk": True, "distanceCm": 100.0, "fovDeg": 90.0}
    r = client.post(f"/inspections/{insp}/detect",
                    files={"image": ("c.jpg", _jpg(), "image/jpeg")}, data={"meta": json.dumps(m)})
    assert r.status_code == 200 and r.json()["cracks"][0]["pos"]["x"] == pytest.approx(1250.0)
