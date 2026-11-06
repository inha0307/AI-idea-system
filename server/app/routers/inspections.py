from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import images
from app.ai import detector
from app.config import settings
from app.db import get_db
from app.models import Crack, Inspection, Structure
from app.schemas import CaptureMeta, DetectedCrack, DetectOut, InspectionCreate, InspectionOut, Vec3

router = APIRouter(tags=["inspections"])


@router.post("/inspections", response_model=InspectionOut, status_code=201)
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
@router.post("/inspections/{inspection_id}/detect", response_model=DetectOut)
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

    # 2. 원본 저장
    rel_dir = f"captures/{inspection_id}"
    orig_rel = f"{rel_dir}/{cap.id}_orig.jpg"
    overlay_rel = f"{rel_dir}/{cap.id}_overlay.jpg"
    images.save_jpg(settings.storage_dir / orig_rel, img)

    # 3. 탐지
    found = detector.detect(img, cap.model_dump(by_alias=True))

    # 4. 결과 이미지 저장
    images.save_jpg(settings.storage_dir / overlay_rel, images.draw_overlay(img, found))

    # 5. 균열 저장 (MVP: 모든 균열 위치 = hit 좌표)
    image_url, overlay_url = f"/files/{orig_rel}", f"/files/{overlay_rel}"
    rows = [
        Crack(
            inspection_id=inspection_id, structure_id=insp.structure_id,
            image_url=image_url, overlay_url=overlay_url,
            pos_x=cap.hit.x, pos_y=cap.hit.y, pos_z=cap.hit.z,
            length_mm=f["lengthMm"], width_mm=f["widthMm"], area_mm2=f["areaMm2"],
            distance_cm=cap.distance_cm, fov_deg=cap.fov_deg, image_width=img.shape[1],
            severity=f["severity"], confidence=f["confidence"],
        )
        for f in found
    ]
    db.add_all(rows)
    db.commit()

    # 6. 응답
    return DetectOut(
        inspection_id=inspection_id,
        cracks=[
            DetectedCrack(
                id=r.id, severity=r.severity, confidence=round(r.confidence, 3),
                length_mm=round(r.length_mm, 1), width_mm=round(r.width_mm, 2),
                area_mm2=round(r.area_mm2, 1),
                distance_cm=r.distance_cm, fov_deg=r.fov_deg, image_width=r.image_width,
                pos=Vec3(x=r.pos_x, y=r.pos_y, z=r.pos_z),
                bbox=f["bbox"], image_url=image_url, overlay_url=overlay_url,
            )
            for r, f in zip(rows, found)
        ],
    )
