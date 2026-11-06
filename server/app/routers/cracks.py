from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Crack
from app.schemas import CrackOut, SeverityThresholds, Vec3

router = APIRouter(tags=["cracks"])


def to_crack_out(c: Crack) -> CrackOut:
    return CrackOut(
        id=c.id, track_id=c.track_id, inspection_id=c.inspection_id, structure_id=c.structure_id,
        severity=c.severity, confidence=c.confidence,
        length_mm=c.length_mm, width_mm=c.width_mm, area_mm2=c.area_mm2,
        distance_cm=c.distance_cm, fov_deg=c.fov_deg, image_width=c.image_width,
        pos=Vec3(x=c.pos_x, y=c.pos_y, z=c.pos_z),
        image_url=c.image_url, overlay_url=c.overlay_url, created_at=c.created_at,
    )


@router.get("/severity-thresholds", response_model=SeverityThresholds)
def get_severity_thresholds():
    """위험도 기준값 (평균 폭 mm). 웹이 측정 해상도와 비교하는 데 씀."""
    return SeverityThresholds(caution_mm=settings.SEVERITY_CAUTION_MM, danger_mm=settings.SEVERITY_DANGER_MM)


def _get_crack(db: Session, crack_id: int) -> Crack:
    c = db.get(Crack, crack_id)
    if c is None:
        raise HTTPException(404, f"균열 {crack_id}이(가) 없습니다")
    return c


@router.get("/cracks/{crack_id}", response_model=CrackOut)
def get_crack(crack_id: int, db: Session = Depends(get_db)):
    return to_crack_out(_get_crack(db, crack_id))


@router.get("/cracks/{crack_id}/history", response_model=list[CrackOut])
def get_crack_history(crack_id: int, db: Session = Depends(get_db)):
    """같은 균열(trackId)의 관측 이력, 오래된 순."""
    c = _get_crack(db, crack_id)
    if c.track_id is None:
        return [to_crack_out(c)]
    rows = db.scalars(
        select(Crack).where(Crack.track_id == c.track_id).order_by(Crack.created_at, Crack.id)
    )
    return [to_crack_out(r) for r in rows]
