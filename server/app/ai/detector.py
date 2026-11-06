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
    # 첫 추론은 준비 때문에 수 초가 걸리므로 서버 시작 때 미리 한 번 돌려 둠
    _model.predict(np.zeros((640, 640, 3), np.uint8), device=_device, verbose=False)
    measure(np.ones((3, 3), bool), 1.0)  # scikit-image도 미리 불러 둠
    log.info("YOLO 모델 로드: %s (%s, task=%s, 클래스=%s)", path, device, _model.task, list(_model.names.values()))


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
            iou=0.5,  # 겹친 박스를 기본값(0.7)보다 적극적으로 합침
            retina_masks=True,  # 마스크를 원본 크기로 → mm 환산 정확
            verbose=False,
        )[0]

    if result.boxes is None or len(result.boxes) == 0:
        return []

    h, w = image.shape[:2]
    scale = mm_per_px(meta["distanceCm"], meta["fovDeg"], w)
    boxes = result.boxes.xyxy.cpu().numpy()
    confs = result.boxes.conf.cpu().numpy()
    # 분할(seg) 모델이면 마스크를 그대로 쓰고, 탐지(detect) 모델이면 박스 안에서 마스크를 추정
    masks = result.masks.data.cpu().numpy() if result.masks is not None else [None] * len(boxes)

    keep = suppress_overlaps(boxes, confs)
    cracks = []
    for mask, box, conf in ((masks[i], boxes[i], confs[i]) for i in keep):
        if mask is None:
            mask = mask_from_box(image, box)
            if not mask.any():
                # 선을 못 찾음 → 박스 대각선을 1픽셀 폭 균열로 봄. 폭이 1픽셀이라 웹에는 「측정 신뢰 불가」로 표시됨
                line = np.zeros((h, w), dtype=np.uint8)
                x1, y1, x2, y2 = (int(round(v)) for v in box)
                cv2.line(line, (x1, y1), (x2, y2), 1, 1)
                mask = line > 0
        else:
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


def suppress_overlaps(boxes, confs, max_overlap: float = 0.7) -> list[int]:
    """한 균열에 박스가 겹쳐 잡힌 경우 신뢰도 높은 것만 남긴 인덱스 (신뢰도 높은 순).

    겹침 = 교집합 ÷ 작은 박스 넓이. 큰 박스 안에 작은 박스가 거의 들어가 있으면 같은 균열로 본다.
    """
    order = sorted(range(len(boxes)), key=lambda i: -float(confs[i]))
    kept: list[int] = []
    for i in order:
        a = boxes[i]
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        dup = False
        for j in kept:
            b = boxes[j]
            iw = min(a[2], b[2]) - max(a[0], b[0])
            ih = min(a[3], b[3]) - max(a[1], b[1])
            if iw <= 0 or ih <= 0:
                continue
            area_b = (b[2] - b[0]) * (b[3] - b[1])
            if iw * ih / max(min(area_a, area_b), 1e-6) >= max_overlap:
                dup = True
                break
        if not dup:
            kept.append(i)
    return kept


def mask_from_box(image: np.ndarray, box) -> np.ndarray:
    """탐지(detect) 모델용: 박스 안에서 주변보다 어두운 선(균열)을 골라 마스크를 만든다.

    균열은 벽보다 어둡고 길다는 점을 이용해, 두 가지로 찾은 것을 합친다.
    A. 가는 균열: black-hat(주변 배경보다 얼마나 어두운지)에서 가장 어두운 정도의 절반 이상 (반치폭)
    B. 굵은 균열 (UE5 데칼처럼 수십 px): 박스 안 벽 밝기와 가장 어두운 밝기의 중간보다 어두운 픽셀
       (A는 커널보다 굵은 균열을 놓치므로 B가 필요)
    그다음 박스 크기에 비해 충분히 길게 이어진 덩어리만 남긴다 (벽 얼룩·잡음 제거).
    분할(seg) 모델이 오면 이 함수는 쓰이지 않는다.
    """
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    mask = np.zeros((h, w), dtype=bool)
    if x2 - x1 < 3 or y2 - y1 < 3:
        return mask

    gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    crop = np.zeros(gray.shape, dtype=np.uint8)

    # A. 가는 균열
    k = max(9, (min(x2 - x1, y2 - y1) // 8) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    dark = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)  # 배경보다 어두운 정도
    dark = cv2.GaussianBlur(dark, (3, 3), 0)
    floor = float(np.median(dark))  # 벽 질감·잡음 수준
    peak = float(np.percentile(dark, 99.8))  # 한두 픽셀 튀는 값에 끌려가지 않게
    if peak - floor >= 25:
        crop |= (dark >= floor + (peak - floor) * 0.5).astype(np.uint8)

    # B. 굵은 균열
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    wall = float(np.percentile(smooth, 75))
    darkest = float(np.percentile(smooth, 0.5))
    if wall - darkest >= 40:
        crop |= (smooth <= (wall + darkest) / 2).astype(np.uint8)

    if not crop.any():  # 잡음과 뚜렷이 구분되는 어두운 선이 없음
        return mask

    n, labels, stats, _ = cv2.connectedComponentsWithStats(crop, connectivity=8)
    min_span = 0.15 * max(x2 - x1, y2 - y1)  # 선이라면 박스 크기의 15% 이상 이어짐
    keep = np.zeros(crop.shape, dtype=bool)
    for i in range(1, n):
        span = max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT])
        if span >= min_span:
            keep |= labels == i
    mask[y1:y2, x1:x2] = keep
    return mask


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
