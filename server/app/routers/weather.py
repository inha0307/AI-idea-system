from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import weather
from app.db import get_db
from app.routers.structures import get_structure_or_404
from app.schemas import WeatherOut

router = APIRouter(tags=["weather"])


# 기상청 호출은 블로킹이라 일반 def로 둬서 스레드풀에서 실행
@router.get("/structures/{structure_id}/weather", response_model=WeatherOut)
def get_structure_weather(structure_id: int, db: Session = Depends(get_db)):
    s = get_structure_or_404(db, structure_id)
    if s.latitude is None or s.longitude is None:
        raise HTTPException(404, f"구조물 {structure_id}의 위치(위경도)가 없습니다")
    try:
        obs, stale = weather.get_weather(s.latitude, s.longitude)
    except weather.WeatherUnavailable as e:
        raise HTTPException(503, f"날씨를 가져올 수 없습니다: {e}")
    return WeatherOut(
        structure_id=s.id,
        temperature_c=obs.temperature_c,
        humidity_pct=obs.humidity_pct,
        wind_speed_ms=obs.wind_speed_ms,
        wind_dir_deg=obs.wind_dir_deg,
        wind_level=weather.wind_level(obs.wind_speed_ms),
        observed_at=obs.observed_at,
        stale=stale,
    )
