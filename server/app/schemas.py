"""API 입출력 형식. JSON 필드는 camelCase (규칙 11)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Severity = Literal["safe", "caution", "danger"]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Vec3(CamelModel):
    x: float
    y: float
    z: float


# ---------- UE5 capture_result (1-4 ①) ----------

class Drone(Vec3):
    yaw: float
    pitch: float


class CaptureMeta(CamelModel):
    type: Literal["capture_result"] = "capture_result"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")  # 파일명에 쓰므로 제한
    structure_id: int
    drone: Drone
    hit: Vec3 | None = None
    hit_ok: bool
    distance_cm: float = Field(gt=0)
    fov_deg: float = Field(gt=0, lt=180)


# ---------- 구조물 ----------

class CrackCounts(BaseModel):
    safe: int = 0
    caution: int = 0
    danger: int = 0


class StructureOut(CamelModel):
    id: int
    name: str
    address: str | None
    model_url: str | None
    latitude: float | None
    longitude: float | None
    created_at: datetime
    crack_counts: CrackCounts
    last_inspected_at: datetime | None


# ---------- 날씨 ----------

class WeatherOut(CamelModel):
    structure_id: int
    temperature_c: float
    humidity_pct: float
    wind_speed_ms: float
    wind_dir_deg: float | None
    wind_level: Literal["weak", "moderate", "strong", "very_strong"]
    observed_at: datetime
    source: Literal["KMA"] = "KMA"
    stale: bool


# ---------- 점검 ----------

class InspectionCreate(CamelModel):
    structure_id: int
    note: str | None = None


class InspectionOut(CamelModel):
    id: int
    structure_id: int
    started_at: datetime
    note: str | None


# ---------- 균열 ----------

class CaptureConditions(CamelModel):
    """촬영 조건. 웹이 1픽셀당 mm를 계산해 측정 신뢰도를 표시하는 데 씀."""
    distance_cm: float | None
    fov_deg: float | None
    image_width: int | None


class CrackOut(CaptureConditions):
    id: int
    inspection_id: int
    structure_id: int
    severity: Severity
    confidence: float
    length_mm: float
    width_mm: float
    area_mm2: float
    pos: Vec3
    image_url: str | None
    overlay_url: str | None
    created_at: datetime


class DetectedCrack(CaptureConditions):
    id: int
    severity: Severity
    confidence: float
    length_mm: float
    width_mm: float
    area_mm2: float
    pos: Vec3
    bbox: list[int]
    image_url: str
    overlay_url: str


class DetectOut(CamelModel):
    # UE5가 이 응답을 Pixel Streaming으로 웹에 그대로 전달하므로 메시지 종류와 요청 id를 포함
    type: Literal["detect_result"] = "detect_result"
    inspection_id: int
    capture_id: str
    cracks: list[DetectedCrack]
