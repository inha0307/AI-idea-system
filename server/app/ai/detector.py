"""균열 탐지 모듈 (AI 담당 관리).

백엔드와의 약속 (시스템팀 문서 2-5):
    load_model(path, device)  서버 시작 시 1번
    detect(image, meta)       image = BGR 원본, meta = capture_result 딕셔너리
    반환: [{ confidence, lengthMm, widthMm, areaMm2, severity, bbox, mask }, ...]

.env의 DETECTOR=fake 이면 고정된 균열 1개를 돌려줍니다 (M2 연결 테스트용).
"""

import logging
import math
import threading
from pathlib import Path

import cv2
import numpy as np

from app.config import settings

log = logging.getLogger(__name__)

_model = None
_device = "cpu"
_lock = threading.Lock()  # 동시 요청이 와도 모델은 한 번에 하나씩


def load_model(path: str | Path, device: str) -> None:
    global _model, _device
    if settings.DETECTOR == "fake":
        log.warning("DETECTOR=fake: 가짜 탐지 결과를 돌려줍니다")
        return

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"모델 파일이 없습니다: {path} (Drive /models에서 받거나 DETECTOR=fake로 실행)"
        )
    from ultralytics import YOLO  # 무거운 라이브러리라 필요할 때만 불러옴

    _model = YOLO(str(path))
    _device = device
    log.info("YOLO 모델 로드: %s (%s)", path, device)


def detect(image: np.ndarray, meta: dict) -> list[dict]:
    if settings.DETECTOR == "fake":
        return _fake_detect(image)
    if _model is None:
        raise RuntimeError("load_model()이 먼저 호출되어야 합니다")

    with _lock:
        result = _model.predict(
            image,
            device=_device,
            conf=settings.CONF_THRESHOLD,
            retina_masks=True,  # 마스크를 원본 크기로 → mm 환산 정확
            verbose=False,
        )[0]

    if result.masks is None:
        return []

    h, w = image.shape[:2]
    scale = mm_per_px(meta["distanceCm"], meta["fovDeg"], w)
    masks = result.masks.data.cpu().numpy()
    boxes = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()

    cracks = []
    for mask, box, conf in zip(masks, boxes, confs):
        mask = mask > 0.5
        if mask.shape != (h, w):
            mask = cv2.resize(mask.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST) > 0
        m = measure(mask, scale)
        cracks.append({
            "confidence": float(conf),
            **m,
            "severity": severity(m["widthMm"], settings.SEVERITY_CAUTION_MM, settings.SEVERITY_DANGER_MM),
            "bbox": [int(round(v)) for v in box],
            "mask": mask,
        })
    return cracks


# ---------- 크기 측정과 위험도 (문서 2-5 계산식) ----------

def mm_per_px(distance_cm: float, fov_deg: float, img_w: int) -> float:
    return 2 * distance_cm * 10 * math.tan(math.radians(fov_deg) / 2) / img_w


def measure(mask: np.ndarray, scale: float) -> dict:
    from skimage.morphology import skeletonize

    length_px = int(skeletonize(mask > 0).sum())
    area_px = int((mask > 0).sum())
    width_px = area_px / max(length_px, 1)
    return {
        "lengthMm": length_px * scale,
        "widthMm": width_px * scale,
        "areaMm2": area_px * scale * scale,
    }


def severity(width_mm: float, caution_mm: float, danger_mm: float) -> str:
    if width_mm >= danger_mm:
        return "danger"
    if width_mm >= caution_mm:
        return "caution"
    return "safe"


# ---------- 가짜 탐지 (M2용) ----------

def _fake_detect(image: np.ndarray) -> list[dict]:
    """문서 1-4 ② 예시 응답과 같은 균열 1개. bbox는 이미지 크기에 맞춰 늘림."""
    h, w = image.shape[:2]
    sx, sy = w / 1920, h / 1080
    x1, y1, x2, y2 = int(812 * sx), int(400 * sy), int(1104 * sx), int(690 * sy)

    mask = np.zeros((h, w), dtype=np.uint8)
    pts = np.array([
        [x1, y1],
        [x1 + (x2 - x1) // 3, y1 + (y2 - y1) // 2],
        [x1 + 2 * (x2 - x1) // 3, y1 + (y2 - y1) // 3],
        [x2, y2],
    ], dtype=np.int32)
    cv2.polylines(mask, [pts], isClosed=False, color=1, thickness=max(3, w // 300))

    return [{
        "confidence": 0.87,
        "lengthMm": 420.5,
        "widthMm": 1.8,
        "areaMm2": 756.9,
        "severity": "caution",
        "bbox": [x1, y1, x2, y2],
        "mask": mask.astype(bool),
    }]
