"""새로 찍힌 균열을 기존 균열(트랙)에 묶기.

규칙 (docs 1-4 ②):
- 같은 구조물에서 반경 TRACK_MATCH_CM 안의 가장 가까운 트랙에 붙인다.
- 길이가 2배 넘게 다르면 다른 균열로 본다 (가까이 붙은 다른 균열이 합쳐지지 않게).
- 한 사진 안의 균열끼리는 같은 트랙에 붙지 않는다 (가까운 쌍부터 하나씩 배정).
"""

import threading
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.geometry import Vec, distance
from app.models import CrackTrack

# 동시에 두 사진이 와도 같은 균열에 트랙이 두 개 생기지 않게 (배정 ~ 커밋까지 잠금)
lock = threading.Lock()

MAX_LENGTH_RATIO = 2.0


def _similar_length(a: float, b: float) -> bool:
    if a <= 0 or b <= 0:
        return True
    return max(a, b) / min(a, b) <= MAX_LENGTH_RATIO


def assign_tracks(
    db: Session, structure_id: int, items: list[tuple[Vec, float]], when: datetime
) -> list[CrackTrack]:
    """items = [(위치, 길이 mm), ...] → 같은 순서의 트랙. 새 트랙은 db에 추가(flush)된다."""
    tracks = list(db.scalars(select(CrackTrack).where(CrackTrack.structure_id == structure_id)))
    radius = settings.TRACK_MATCH_CM

    pairs = []
    for i, (pos, length) in enumerate(items):
        for t in tracks:
            d = distance(pos, (t.pos_x, t.pos_y, t.pos_z))
            if d <= radius and _similar_length(length, t.length_mm):
                pairs.append((d, i, t.id, t))
    pairs.sort(key=lambda p: p[:3])

    chosen: dict[int, CrackTrack] = {}
    used: set[int] = set()
    for _, i, tid, t in pairs:
        if i not in chosen and tid not in used:
            chosen[i] = t
            used.add(tid)

    result = []
    for i, (pos, length) in enumerate(items):
        t = chosen.get(i)
        if t is None:
            t = CrackTrack(structure_id=structure_id, pos_x=0.0, pos_y=0.0, pos_z=0.0,
                           observations=0, first_seen_at=when)
            db.add(t)
        n = t.observations or 0
        t.pos_x = (t.pos_x * n + pos[0]) / (n + 1)
        t.pos_y = (t.pos_y * n + pos[1]) / (n + 1)
        t.pos_z = (t.pos_z * n + pos[2]) / (n + 1)
        t.observations = n + 1
        t.length_mm = length
        t.last_seen_at = when
        result.append(t)
    db.flush()
    return result
