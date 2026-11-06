"""사진 속 픽셀 → UE5 벽 위의 3D 좌표 (cm).

UE5 좌표계: X 앞, Y 오른쪽, Z 위 (왼손). 카메라 roll은 0으로 가정한다.
- 카메라 위치 = hit − 정면방향 × distanceCm  (Line Trace가 카메라 정면으로 나갔으므로)
- 픽셀마다 광선을 만들고, hit을 지나는 벽 평면과 만나는 점을 구한다.
- 벽 방향(hitNormal)이 없으면 벽이 카메라를 정면으로 마주 본다고 가정한다.
"""

import math

Vec = tuple[float, float, float]


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec) -> Vec:
    n = math.sqrt(_dot(a, a))
    return _scale(a, 1 / n) if n > 0 else a


def camera_basis(yaw_deg: float, pitch_deg: float) -> tuple[Vec, Vec, Vec]:
    """(정면, 오른쪽, 위) 단위 벡터. UE5 FRotator(pitch, yaw, roll=0)와 같은 방향."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    forward = (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))
    right = (-math.sin(y), math.cos(y), 0.0)
    up = (-math.sin(p) * math.cos(y), -math.sin(p) * math.sin(y), math.cos(p))
    return forward, right, up


def pixel_to_wall(meta: dict, px: float, py: float, img_w: int, img_h: int) -> Vec:
    """meta = capture_result (camelCase). (px, py) = 픽셀 좌표 (왼쪽 위가 0, 0)."""
    drone, hit_d = meta["drone"], meta["hit"]
    forward, right, up = camera_basis(drone["yaw"], drone["pitch"])
    hit = (hit_d["x"], hit_d["y"], hit_d["z"])
    cam = _add(hit, _scale(forward, -meta["distanceCm"]))

    # 픽셀 방향의 광선 (가로 화각 기준, 정사각 픽셀)
    t = math.tan(math.radians(meta["fovDeg"]) / 2)
    half = img_w / 2
    tx = (px - img_w / 2) / half * t
    ty = (py - img_h / 2) / half * t
    ray = _add(_add(forward, _scale(right, tx)), _scale(up, -ty))

    frontal = _scale(forward, -1.0)
    n_d = meta.get("hitNormal")
    normal = _norm((n_d["x"], n_d["y"], n_d["z"])) if n_d else frontal

    to_hit = _add(hit, _scale(cam, -1.0))
    for n in (normal, frontal):  # 벽과 거의 평행한 광선이면 정면 가정으로 다시
        denom = _dot(ray, n)
        if abs(denom) < 1e-3:
            continue
        s = _dot(to_hit, n) / denom
        if s > 0:
            return _add(cam, _scale(ray, s))
    return hit


def distance(a: Vec, b: Vec) -> float:
    d = _add(a, _scale(b, -1.0))
    return math.sqrt(_dot(d, d))
