import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import auth, images, tracking
from app.ai import detector
from app.config import settings
from app.db import get_db
from app.geometry import pixel_to_wall
from app.models import Crack, Inspection, Structure
from app.schemas import CaptureMeta, DetectedCrack, DetectOut, InspectionCreate, InspectionOut, Vec3

router = APIRouter(tags=["inspections"])


@router.post("/inspections", response_model=InspectionOut, status_code=201,
             dependencies=[Depends(auth.require_admin)])
def create_inspection(body: InspectionCreate, db: Session = Depends(get_db)):
    if db.get(Structure, body.structure_id) is None:
        raise HTTPException(404, f"구조물 {body.structure_id}이(가) 없습니다")
    insp = Inspection(structure_id=body.structure_id, note=body.note)
    db.add(insp)
    db.commit()
    return InspectionOut(
        id=insp.id, structure_id=insp.structure_id, started_at=insp.started_at, note=insp.note
    )


# 추론이 CPU를 오래 쓰므로 일반 def로 둬서 스레드풀에서 실행되게 함
# UE5가 호출하므로 토큰 없이, 로컬 네트워크에서만 허용 (터널 거부)
@router.post("/inspections/{inspection_id}/detect", response_model=DetectOut,
             dependencies=[Depends(auth.require_local)])
def detect(
    inspection_id: int,
    image: UploadFile = File(..., description="스트리밍 화면 캡처 JPG"),
    meta: str = Form(..., description="capture_result JSON 문자열 그대로"),
    db: Session = Depends(get_db),
):
    # 1. 입력 확인
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(404, f"점검 {inspection_id}이(가) 없습니다")
    try:
        cap = CaptureMeta.model_validate_json(meta)
    except ValidationError as e:
        raise HTTPException(422, {"message": "meta 형식이 잘못되었습니다", "errors": e.errors(include_url=False)})
    if cap.structure_id != insp.structure_id:
        raise HTTPException(
            422, f"meta.structureId({cap.structure_id})가 점검의 구조물({insp.structure_id})과 다릅니다"
        )
    if not cap.hit_ok or cap.hit is None:
        raise HTTPException(422, "hitOk=false: 구조물을 향해 주세요")

    img = images.decode(image.file.read())
    if img is None:
        raise HTTPException(422, "이미지를 읽을 수 없습니다 (JPG/PNG만 가능)")

    # 2. 원본과 meta 저장 (meta는 나중에 다시 분석할 때 씀)
    rel_dir = f"captures/{inspection_id}"
    orig_rel = f"{rel_dir}/{cap.id}_orig.jpg"
    overlay_rel = f"{rel_dir}/{cap.id}_overlay.jpg"
    images.save_jpg(settings.storage_dir / orig_rel, img)
    meta_dict = cap.model_dump(by_alias=True)
    (settings.storage_dir / rel_dir / f"{cap.id}_meta.json").write_text(
        json.dumps(meta_dict, ensure_ascii=False), encoding="utf-8"
    )

    # 3. 탐지
    found = detector.detect(img, meta_dict)

    # 4. 결과 이미지 저장
    images.save_jpg(settings.storage_dir / overlay_rel, images.draw_overlay(img, found))

    # 5. 균열마다 벽 위치를 계산하고 같은 균열(트랙)에 묶어서 저장
    h, w = img.shape[:2]
    positions = [
        pixel_to_wall(meta_dict, (f["bbox"][0] + f["bbox"][2]) / 2, (f["bbox"][1] + f["bbox"][3]) / 2, w, h)
        for f in found
    ]
    image_url, overlay_url = f"/files/{orig_rel}", f"/files/{overlay_rel}"
    now = datetime.now(timezone.utc)
    with tracking.lock:
        tracks = tracking.assign_tracks(
            db, insp.structure_id, [(p, f["lengthMm"]) for p, f in zip(positions, found)], now
        )
        rows = [
            Crack(
                inspection_id=inspection_id, structure_id=insp.structure_id, track_id=t.id,
                image_url=image_url, overlay_url=overlay_url,
                pos_x=p[0], pos_y=p[1], pos_z=p[2],
                length_mm=f["lengthMm"], width_mm=f["widthMm"], area_mm2=f["areaMm2"],
                distance_cm=cap.distance_cm, fov_deg=cap.fov_deg, image_width=w,
                severity=f["severity"], confidence=f["confidence"], created_at=now,
            )
            for p, f, t in zip(positions, found, tracks)
        ]
        db.add_all(rows)
        db.commit()

    # 6. 응답
    return DetectOut(
        inspection_id=inspection_id,
        capture_id=cap.id,
        cracks=[
            DetectedCrack(
                id=r.id, track_id=r.track_id, severity=r.severity, confidence=round(r.confidence, 3),
                length_mm=round(r.length_mm, 1), width_mm=round(r.width_mm, 2),
                area_mm2=round(r.area_mm2, 1),
                distance_cm=r.distance_cm, fov_deg=r.fov_deg, image_width=r.image_width,
                pos=Vec3(x=round(r.pos_x, 1), y=round(r.pos_y, 1), z=round(r.pos_z, 1)),
                bbox=f["bbox"], image_url=image_url, overlay_url=overlay_url,
            )
            for r, f in zip(rows, found)
        ],
    )
