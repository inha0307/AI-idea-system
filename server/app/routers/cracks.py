from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Crack
from app.schemas import CrackOut, Vec3

router = APIRouter(tags=["cracks"])


def to_crack_out(c: Crack) -> CrackOut:
    return CrackOut(
        id=c.id, inspection_id=c.inspection_id, structure_id=c.structure_id,
        severity=c.severity, confidence=c.confidence,
        length_mm=c.length_mm, width_mm=c.width_mm, area_mm2=c.area_mm2,
        distance_cm=c.distance_cm, fov_deg=c.fov_deg, image_width=c.image_width,
        pos=Vec3(x=c.pos_x, y=c.pos_y, z=c.pos_z),
        image_url=c.image_url, overlay_url=c.overlay_url, created_at=c.created_at,
    )


@router.get("/cracks/{crack_id}", response_model=CrackOut)
def get_crack(crack_id: int, db: Session = Depends(get_db)):
    c = db.get(Crack, crack_id)
    if c is None:
        raise HTTPException(404, f"균열 {crack_id}이(가) 없습니다")
    return to_crack_out(c)
