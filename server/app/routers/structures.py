from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Crack, Inspection, Structure
from app.routers.cracks import to_crack_out
from app.schemas import CrackCounts, CrackOut, LatestInspection, Severity, StructureOut

router = APIRouter(tags=["structures"])


def latest_per_track(*conds, per_inspection: bool = False):
    """조건에 맞는 관측 중 균열(트랙)마다 가장 최근 1건의 id 서브쿼리.

    트랙이 없는 관측(옛 데이터)은 각자 하나의 균열로 본다.
    per_inspection=True면 점검마다 따로 고른다 (점검별 균열 수).
    """
    track_key = func.coalesce(Crack.track_id, -Crack.id)
    partition = [Crack.inspection_id, track_key] if per_inspection else [track_key]
    rn = func.row_number().over(
        partition_by=partition, order_by=(Crack.created_at.desc(), Crack.id.desc())
    ).label("rn")
    sub = select(Crack.id.label("id"), rn).where(*conds).subquery()
    return select(sub.c.id).where(sub.c.rn == 1)


def _structure_outs(db: Session, structures: list[Structure]) -> list[StructureOut]:
    ids = [s.id for s in structures]
    # 실제 균열 수: 균열마다 가장 최근 관측의 위험도로 셈
    counts: dict[int, CrackCounts] = {i: CrackCounts() for i in ids}
    for sid, sev, n in db.execute(
        select(Crack.structure_id, Crack.severity, func.count())
        .where(Crack.id.in_(latest_per_track(Crack.structure_id.in_(ids))))
        .group_by(Crack.structure_id, Crack.severity)
    ):
        setattr(counts[sid], sev, n)

    last = dict(db.execute(
        select(Inspection.structure_id, func.max(Inspection.started_at))
        .where(Inspection.structure_id.in_(ids))
        .group_by(Inspection.structure_id)
    ).all())

    latest = _latest_inspections(db, ids)

    return [
        StructureOut(
            id=s.id, name=s.name, address=s.address, model_url=s.model_url,
            latitude=s.latitude, longitude=s.longitude,
            created_at=s.created_at, crack_counts=counts[s.id],
            last_inspected_at=last.get(s.id),
            latest_inspection=latest.get(s.id),
        )
        for s in structures
    ]


def _latest_inspections(db: Session, ids: list[int]) -> dict[int, LatestInspection]:
    """구조물별로 균열이 1건 이상 있는 가장 최근 점검과, 그 점검에서 찍힌 균열 수 (같은 균열은 1개)."""
    rows = db.execute(
        select(Inspection.id, Inspection.structure_id, Inspection.started_at)
        .where(Inspection.structure_id.in_(ids))
        .where(select(Crack.id).where(Crack.inspection_id == Inspection.id).exists())
        .order_by(Inspection.started_at.desc(), Inspection.id.desc())
    ).all()
    latest: dict[int, LatestInspection] = {}
    for insp_id, sid, started_at in rows:
        if sid not in latest:
            latest[sid] = LatestInspection(id=insp_id, started_at=started_at, crack_counts=CrackCounts())
    if not latest:
        return latest

    by_insp = {li.id: li for li in latest.values()}
    picked = latest_per_track(Crack.inspection_id.in_(by_insp), per_inspection=True)
    for insp_id, sev, n in db.execute(
        select(Crack.inspection_id, Crack.severity, func.count())
        .where(Crack.id.in_(picked))
        .group_by(Crack.inspection_id, Crack.severity)
    ):
        setattr(by_insp[insp_id].crack_counts, sev, n)
    return latest


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
    structure_id: int,
    severity: Severity | None = None,
    inspection_id: int | None = Query(None, alias="inspectionId"),
    latest: bool = Query(False, description="균열(trackId)마다 가장 최근 관측 1건만"),
    db: Session = Depends(get_db),
):
    get_structure_or_404(db, structure_id)
    conds = [Crack.structure_id == structure_id]
    if severity:
        conds.append(Crack.severity == severity)
    if inspection_id is not None:
        conds.append(Crack.inspection_id == inspection_id)
    q = select(Crack).where(*conds)
    if latest:
        q = select(Crack).where(Crack.id.in_(latest_per_track(*conds)))
    return [to_crack_out(c) for c in db.scalars(q.order_by(Crack.created_at.desc(), Crack.id.desc()))]
