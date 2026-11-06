from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Crack, Inspection, Structure
from app.routers.cracks import to_crack_out
from app.schemas import CrackCounts, CrackOut, Severity, StructureOut

router = APIRouter(tags=["structures"])


def _structure_outs(db: Session, structures: list[Structure]) -> list[StructureOut]:
    ids = [s.id for s in structures]
    counts: dict[int, CrackCounts] = {i: CrackCounts() for i in ids}
    for sid, sev, n in db.execute(
        select(Crack.structure_id, Crack.severity, func.count())
        .where(Crack.structure_id.in_(ids))
        .group_by(Crack.structure_id, Crack.severity)
    ):
        setattr(counts[sid], sev, n)

    last = dict(db.execute(
        select(Inspection.structure_id, func.max(Inspection.started_at))
        .where(Inspection.structure_id.in_(ids))
        .group_by(Inspection.structure_id)
    ).all())

    return [
        StructureOut(
            id=s.id, name=s.name, address=s.address, model_url=s.model_url,
            latitude=s.latitude, longitude=s.longitude,
            created_at=s.created_at, crack_counts=counts[s.id],
            last_inspected_at=last.get(s.id),
        )
        for s in structures
    ]


def get_structure_or_404(db: Session, structure_id: int) -> Structure:
    s = db.get(Structure, structure_id)
    if s is None:
        raise HTTPException(404, f"구조물 {structure_id}이(가) 없습니다")
    return s


@router.get("/structures", response_model=list[StructureOut])
def list_structures(db: Session = Depends(get_db)):
    structures = list(db.scalars(select(Structure).order_by(Structure.id)))
    return _structure_outs(db, structures)


@router.get("/structures/{structure_id}", response_model=StructureOut)
def get_structure(structure_id: int, db: Session = Depends(get_db)):
    return _structure_outs(db, [get_structure_or_404(db, structure_id)])[0]


@router.get("/structures/{structure_id}/cracks", response_model=list[CrackOut])
def list_structure_cracks(
    structure_id: int, severity: Severity | None = None, db: Session = Depends(get_db)
):
    get_structure_or_404(db, structure_id)
    q = select(Crack).where(Crack.structure_id == structure_id)
    if severity:
        q = q.where(Crack.severity == severity)
    return [to_crack_out(c) for c in db.scalars(q.order_by(Crack.created_at.desc(), Crack.id.desc()))]
