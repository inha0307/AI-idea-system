"""detector: 탐지(detect) 모델처럼 박스만 올 때의 마스크 추정과 측정.

실제 YOLO 모델은 쓰지 않고 가짜 예측 결과로 대체합니다.
"""

import cv2
import numpy as np
import pytest

pytest.importorskip("skimage")  # requirements-ai.txt

from app.ai import detector  # noqa: E402
from app.config import settings  # noqa: E402


def wall_with_crack(thick: int) -> tuple[np.ndarray, list[int]]:
    img = np.full((1080, 1920, 3), 150, np.uint8)
    noise = np.random.default_rng(0).normal(0, 6, img.shape[:2])
    img = np.clip(img + noise[..., None], 0, 255).astype(np.uint8)
    cv2.line(img, (700, 400), (1200, 650), (45, 45, 45), thick)
    return img, [660, 360, 1240, 690]


def drawn_line_mask(thick: int) -> np.ndarray:
    m = np.zeros((1080, 1920), np.uint8)
    cv2.line(m, (700, 400), (1200, 650), 1, thick)
    return m > 0


@pytest.mark.parametrize("thick", [3, 6, 10])
def test_mask_from_box_matches_drawn_line(thick):
    """박스 안에서 추정한 마스크가 실제로 그린 균열 선과 같은 측정값을 내는지."""
    img, box = wall_with_crack(thick)
    est = detector.measure(detector.mask_from_box(img, box), 1.0)  # 1 px = 1 mm
    ideal = detector.measure(drawn_line_mask(thick), 1.0)
    assert est["widthMm"] == pytest.approx(ideal["widthMm"], rel=0.1)
    assert est["lengthMm"] == pytest.approx(ideal["lengthMm"], rel=0.1)


def test_mask_from_box_thick_decal_crack():
    """UE5 데칼처럼 수십 px 굵기의 균열도 잡는지 (가는 균열용 방식만으로는 놓침)."""
    img = np.full((1080, 1920, 3), 205, np.uint8)
    cv2.line(img, (600, 480), (1180, 520), (40, 38, 36), 30)
    est = detector.measure(detector.mask_from_box(img, [570, 420, 1210, 580]), 1.0)
    m = np.zeros((1080, 1920), np.uint8)
    cv2.line(m, (600, 480), (1180, 520), 1, 30)
    ideal = detector.measure(m > 0, 1.0)
    assert est["widthMm"] == pytest.approx(ideal["widthMm"], rel=0.1)


def test_suppress_overlaps_keeps_best_of_nested_boxes():
    boxes = np.array([[568, 438, 1195, 540], [570, 418, 1203, 562], [100, 100, 300, 200]], float)
    confs = np.array([0.40, 0.59, 0.30])
    assert detector.suppress_overlaps(boxes, confs) == [1, 2]  # 겹친 0.40 박스는 버림


def test_mask_from_box_blank_wall_is_empty():
    flat = np.full((1080, 1920, 3), 150, np.uint8)
    assert not detector.mask_from_box(flat, [100, 100, 600, 400]).any()
    noisy, _ = wall_with_crack(3)  # 균열 밖 영역: 벽 잡음만 있음
    assert not detector.mask_from_box(noisy, [100, 100, 400, 300]).any()


class _T:  # torch 텐서 흉내 (.cpu().numpy())
    def __init__(self, a):
        self.a = np.asarray(a, dtype=np.float32)

    def cpu(self):
        return self

    def numpy(self):
        return self.a


class _Boxes:
    def __init__(self, xyxy, conf):
        self.xyxy, self.conf = _T(xyxy), _T(conf)

    def __len__(self):
        return len(self.xyxy.a)


class _Result:
    def __init__(self, xyxy, conf):
        self.boxes, self.masks = _Boxes(xyxy, conf), None  # detect 모델: 마스크 없음


class _FakeYolo:
    def __init__(self, xyxy, conf):
        self.r = _Result(xyxy, conf)

    def predict(self, image, **kw):
        return [self.r]


META = {"distanceCm": 100.0, "fovDeg": 30.0}  # 1 px ≈ 0.279 mm


def test_detect_with_box_only_model(monkeypatch):
    img, box = wall_with_crack(8)
    monkeypatch.setattr(settings, "DETECTOR", "yolo")
    monkeypatch.setattr(detector, "_model", _FakeYolo([box, [100, 100, 400, 300]], [0.9, 0.4]))

    found = detector.detect(img, META)
    assert len(found) == 2
    scale = detector.mm_per_px(100.0, 30.0, 1920)

    real = found[0]
    assert real["bbox"] == box and real["confidence"] == pytest.approx(0.9)
    ideal = detector.measure(drawn_line_mask(8), scale)
    assert real["widthMm"] == pytest.approx(ideal["widthMm"], rel=0.1)
    assert real["severity"] == detector.severity(real["widthMm"], settings.SEVERITY_CAUTION_MM, settings.SEVERITY_DANGER_MM)

    # 빈 벽 박스: 선을 못 찾으면 1픽셀 폭 (웹에서 「측정 신뢰 불가」)
    blank = found[1]
    assert blank["widthMm"] == pytest.approx(scale, rel=0.2)
    assert blank["mask"].any()
