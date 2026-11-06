"""이미지 읽기·저장·결과 그리기.

cv2.imread/imwrite는 Windows의 한글 경로에서 실패하므로 바이트로 직접 읽고 씁니다.
"""

from pathlib import Path

import cv2
import numpy as np

# BGR
SEVERITY_COLORS = {
    "safe": (80, 175, 76),
    "caution": (0, 200, 255),
    "danger": (60, 60, 230),
}


def decode(data: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def save_jpg(path: Path, image: np.ndarray, quality: int = 92) -> None:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"JPG 인코딩 실패: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.tobytes())


def draw_overlay(image: np.ndarray, cracks: list[dict]) -> np.ndarray:
    out = image.copy()
    thick = max(2, image.shape[1] // 640)
    for c in cracks:
        color = SEVERITY_COLORS[c["severity"]]
        mask = c.get("mask")
        if mask is not None:
            m = mask.astype(bool)
            out[m] = (0.4 * out[m] + 0.6 * np.array(color)).astype(np.uint8)
        x1, y1, x2, y2 = c["bbox"]
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thick)
        label = f"{c['severity']} {c['widthMm']:.1f}mm {c['confidence']:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6 * thick / 2, thick)
        ty = max(y1, th + 6)
        cv2.rectangle(out, (x1, ty - th - 6), (x1 + tw + 6, ty), color, -1)
        cv2.putText(out, label, (x1 + 3, ty - 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6 * thick / 2, (0, 0, 0), thick, cv2.LINE_AA)
    return out
