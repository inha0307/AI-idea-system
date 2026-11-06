from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Structure(Base):
    __tablename__ = "structures"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    model_url: Mapped[str | None] = mapped_column(String(500))
    # 날씨 조회용 위치 (WGS84). 없으면 /weather는 404
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    inspections: Mapped[list["Inspection"]] = relationship(back_populates="structure")


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(primary_key=True)
    structure_id: Mapped[int] = mapped_column(ForeignKey("structures.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    note: Mapped[str | None] = mapped_column(String(500))

    structure: Mapped[Structure] = relationship(back_populates="inspections")


class CrackTrack(Base):
    """같은 균열. 여러 사진(관측, cracks 행)이 하나의 트랙에 묶인다."""
    __tablename__ = "crack_tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    structure_id: Mapped[int] = mapped_column(ForeignKey("structures.id"), index=True)
    # 대표 위치 = 관측 위치의 평균 (UE5 cm)
    pos_x: Mapped[float] = mapped_column(Float, default=0.0)
    pos_y: Mapped[float] = mapped_column(Float, default=0.0)
    pos_z: Mapped[float] = mapped_column(Float, default=0.0)
    length_mm: Mapped[float] = mapped_column(Float, default=0.0)  # 가장 최근 관측
    observations: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Crack(Base):
    """균열 관측 1건 (사진 1장에서 찍힌 균열)."""
    __tablename__ = "cracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id"), index=True)
    structure_id: Mapped[int] = mapped_column(ForeignKey("structures.id"), index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("crack_tracks.id"), index=True)
    image_url: Mapped[str | None] = mapped_column(String(500))
    overlay_url: Mapped[str | None] = mapped_column(String(500))
    pos_x: Mapped[float] = mapped_column(Float)
    pos_y: Mapped[float] = mapped_column(Float)
    pos_z: Mapped[float] = mapped_column(Float)
    length_mm: Mapped[float] = mapped_column(Float)
    width_mm: Mapped[float] = mapped_column(Float)
    area_mm2: Mapped[float] = mapped_column(Float)
    # 촬영 조건 (1픽셀당 mm 계산용). 모르면 NULL
    distance_cm: Mapped[float | None] = mapped_column(Float)
    fov_deg: Mapped[float | None] = mapped_column(Float)
    image_width: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
