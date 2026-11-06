"""시연 데이터 생성 (규칙 6: 시연 데이터는 이 스크립트로만 만든다).

실행: python seed.py
⚠️ 기존 DB 테이블을 모두 지우고 다시 만듭니다.
"""

import sys
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from app import images
from app.ai.detector import mm_per_px
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import Crack, CrackTrack, Inspection, Structure

# 구조물 1개: 시연 건물 강릉 스카이베이. .glb는 Drive /assets에서 받아 storage/assets/structure.glb에 둡니다.
# 위경도는 날씨 조회용 (기상청 격자 93, 133). 격자가 5km 단위라 대략적인 값이면 충분합니다.
STRUCTURE = {
    "name": "스카이베이 경포",
    "address": "강원특별자치도 강릉시 해안로 476",
    "model_url": "/files/assets/structure.glb",
    "latitude": 37.8014,
    "longitude": 128.9088,
}

IMG_W, IMG_H = 1920, 1080

# 촬영 조건 (거리 cm, 가로 화각 도)
NEAR = (100.0, 30.0)   # 1픽셀 ≈ 0.28mm → 측정 가능
CLOSE = (50.0, 30.0)   # 1픽셀 ≈ 0.14mm → 측정 가능
FAR = (430.0, 90.0)    # 1픽셀 ≈ 4.48mm → 폭이 1픽셀보다 작아 「측정 신뢰 불가」

# 예시 균열 (UE5 좌표 cm). 정답 위치 목록이 나오면 그 값으로 바꿉니다.
CRACKS = [
    # severity, pos(x, y, z), length_mm, width_mm, confidence, (distance_cm, fov_deg)
    ("danger", (1250.0, -150.0, 380.0), 380.0, 2.4, 0.93, NEAR),
    ("danger", (1250.0, 420.0, 610.0), 640.0, 1.6, 0.88, FAR),
    ("caution", (1250.0, -600.0, 250.0), 250.0, 0.6, 0.87, NEAR),
    ("caution", (1250.0, 120.0, 820.0), 310.0, 0.45, 0.81, FAR),
    ("safe", (1250.0, -320.0, 150.0), 120.0, 0.15, 0.76, CLOSE),
    ("safe", (1250.0, 700.0, 450.0), 95.0, 0.1, 0.71, FAR),
]


def wall_image(seed: int, sev: str, length_mm: float, width_mm: float,
               scale: float) -> tuple[np.ndarray, np.ndarray]:
    """회색 콘크리트 벽 + 균열 선. 선의 길이·굵기는 촬영 조건(scale = 1픽셀당 mm)에 맞춤."""
    rng = np.random.default_rng(seed)
    h, w = IMG_H, IMG_W
    base = rng.normal(150, 12, (h, w)).clip(0, 255).astype(np.uint8)
    img = cv2.cvtColor(cv2.GaussianBlur(base, (5, 5), 0), cv2.COLOR_GRAY2BGR)

    length_px = min(length_mm / scale, w * 0.8)
    steps = 12
    step = length_px / steps
    angles = np.radians(30 + rng.uniform(-25, 25, steps))
    offsets = np.stack([np.cos(angles), np.sin(angles)], axis=1) * step
    path = np.vstack([[0, 0], np.cumsum(offsets, axis=0)])
    path += np.array([w, h]) / 2 - (path.min(axis=0) + path.max(axis=0)) / 2
    pts = path.round().astype(np.int32)
    thick = max(1, round(width_mm / scale))
    cv2.polylines(img, [pts], False, (40, 40, 40), thick, cv2.LINE_AA)

    mask = np.zeros((h, w), np.uint8)
    cv2.polylines(mask, [pts], False, 1, thick)
    x1, y1 = pts.min(axis=0) - 10
    x2, y2 = pts.max(axis=0) + 10
    overlay = images.draw_overlay(img, [{
        "severity": sev, "mask": mask.astype(bool), "bbox": [int(x1), int(y1), int(x2), int(y2)],
        "widthMm": width_mm, "confidence": 0.9,
    }])
    return img, overlay


def main() -> None:
    if "--yes" not in sys.argv:
        ans = input(f"DB({settings.DATABASE_URL})의 모든 데이터를 지우고 다시 만듭니다. 계속할까요? [y/N] ")
        if ans.strip().lower() != "y":
            print("취소했습니다.")
            return

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        s = Structure(**STRUCTURE, created_at=now - timedelta(days=30))
        db.add(s)
        db.flush()

        # 점검 2회: 2주 전 점검에서 1번 균열을 먼저 발견 → 어제 점검에서 폭이 커진 것을 다시 관측
        earlier = Inspection(structure_id=s.id, started_at=now - timedelta(days=14), note="시드 데이터 (이전 점검)")
        insp = Inspection(structure_id=s.id, started_at=now - timedelta(days=1), note="시드 데이터")
        db.add_all([earlier, insp])
        db.flush()

        def observe(insp_obj, track, key, sev, pos, length, width, conf, dist, fov, seed_no):
            orig, overlay = wall_image(seed_no, sev, length, width, mm_per_px(dist, fov, IMG_W))
            rel = f"captures/{insp_obj.id}/{key}"
            images.save_jpg(settings.storage_dir / f"{rel}_orig.jpg", orig)
            images.save_jpg(settings.storage_dir / f"{rel}_overlay.jpg", overlay)
            db.add(Crack(
                inspection_id=insp_obj.id, structure_id=s.id, track_id=track.id,
                image_url=f"/files/{rel}_orig.jpg", overlay_url=f"/files/{rel}_overlay.jpg",
                pos_x=pos[0], pos_y=pos[1], pos_z=pos[2],
                length_mm=length, width_mm=width, area_mm2=round(length * width, 1),
                distance_cm=dist, fov_deg=fov, image_width=IMG_W,
                severity=sev, confidence=conf, created_at=insp_obj.started_at,
            ))

        for i, (sev, pos, length, width, conf, (dist, fov)) in enumerate(CRACKS, start=1):
            first = earlier if i == 1 else insp
            track = CrackTrack(
                structure_id=s.id, pos_x=pos[0], pos_y=pos[1], pos_z=pos[2], length_mm=length,
                observations=2 if i == 1 else 1,
                first_seen_at=first.started_at, last_seen_at=insp.started_at,
            )
            db.add(track)
            db.flush()
            if i == 1:  # 이전 관측: 같은 위치 근처, 폭이 더 가늘었음 (주의)
                observe(earlier, track, "seed-01-prev", "caution", (pos[0], pos[1] + 6, pos[2] - 4),
                        length * 0.9, 0.8, 0.88, dist, fov, 101)
            observe(insp, track, f"seed-{i:02d}", sev, pos, length, width, conf, dist, fov, i)
        db.commit()

    print(f"완료: 구조물 1개, 점검 2회, 균열 {len(CRACKS)}개 (관측 {len(CRACKS) + 1}건, 1번 균열은 2회 관측)")


if __name__ == "__main__":
    main()
