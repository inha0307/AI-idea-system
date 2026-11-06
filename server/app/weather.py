"""기상청 초단기실황으로 구조물 위치의 기온·습도·풍속 조회.

- 공공데이터포털 「기상청_단기예보 조회서비스」 → 초단기실황조회 (getUltraSrtNcst)
- 키는 .env의 KMA_SERVICE_KEY (규칙 8). 오류 메시지에 키가 섞이지 않게 URL은 남기지 않음
- 격자(nx, ny)별로 WEATHER_CACHE_SEC 동안 캐시. 기상청 오류 시 마지막 값을 stale로 돌려줌
"""

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings

KST = timezone(timedelta(hours=9))
NOWCAST_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst"


class WeatherUnavailable(Exception):
    """날씨를 가져올 수 없음 (키 없음, 기상청 오류, 관측값 없음)."""


@dataclass(frozen=True)
class Observation:
    temperature_c: float
    humidity_pct: float
    wind_speed_ms: float
    wind_dir_deg: float | None
    observed_at: datetime


# ---------- 위경도 → 기상청 격자 (기상청 공개 LCC 변환식) ----------

def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    re_km, grid_km = 6371.00877, 5.0
    slat1, slat2, olon, olat = 30.0, 60.0, 126.0, 38.0
    xo, yo = 43, 136
    d = math.pi / 180

    re = re_km / grid_km
    s1, s2, olon_r, olat_r = slat1 * d, slat2 * d, olon * d, olat * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(
        math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2)
    )
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re * sf / math.tan(math.pi / 4 + olat_r / 2) ** sn
    ra = re * sf / math.tan(math.pi / 4 + lat * d / 2) ** sn
    theta = lon * d - olon_r
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    theta *= sn
    return (
        math.floor(ra * math.sin(theta) + xo + 0.5),
        math.floor(ro - ra * math.cos(theta) + yo + 0.5),
    )


def wind_level(speed_ms: float) -> str:
    """기상청 풍속 구분: 4 미만 약 / 4~9 약간 강 / 9~14 강 / 14 이상 매우 강."""
    if speed_ms < 4:
        return "weak"
    if speed_ms < 9:
        return "moderate"
    if speed_ms < 14:
        return "strong"
    return "very_strong"


# ---------- 기상청 호출 ----------

def fetch_nowcast(nx: int, ny: int, now: datetime | None = None) -> Observation:
    """가장 최근 정시 실황. 아직 안 나왔으면(NO_DATA) 1시간 전 것을 씀."""
    if not settings.KMA_SERVICE_KEY:
        raise WeatherUnavailable("KMA_SERVICE_KEY가 설정되지 않았습니다")

    now = now or datetime.now(KST)
    for hours_back in (0, 1):
        base = now - timedelta(hours=hours_back)
        params = {
            "serviceKey": settings.KMA_SERVICE_KEY,
            "pageNo": 1,
            "numOfRows": 20,
            "dataType": "JSON",
            "base_date": base.strftime("%Y%m%d"),
            "base_time": base.strftime("%H00"),
            "nx": nx,
            "ny": ny,
        }
        try:
            r = httpx.get(NOWCAST_URL, params=params, timeout=5)
            body = r.json()  # 키 오류 등은 XML로 와서 여기서 실패
            header = body["response"]["header"]
        except Exception as e:
            raise WeatherUnavailable(f"기상청 응답 오류 ({type(e).__name__})") from None

        code = header.get("resultCode")
        if code == "03":  # NO_DATA: 이번 정시 실황이 아직 없음
            continue
        if code != "00":
            raise WeatherUnavailable(f"기상청 오류 {code}: {header.get('resultMsg')}")
        return _parse(body["response"]["body"]["items"]["item"])

    raise WeatherUnavailable("기상청 관측값이 아직 없습니다")


def _parse(items: list[dict]) -> Observation:
    values = {it["category"]: it["obsrValue"] for it in items}
    try:
        first = items[0]
        observed_at = datetime.strptime(first["baseDate"] + first["baseTime"], "%Y%m%d%H%M").replace(tzinfo=KST)
        return Observation(
            temperature_c=float(values["T1H"]),
            humidity_pct=float(values["REH"]),
            wind_speed_ms=float(values["WSD"]),
            wind_dir_deg=float(values["VEC"]) if "VEC" in values else None,
            observed_at=observed_at,
        )
    except (KeyError, ValueError, IndexError):
        raise WeatherUnavailable("기상청 응답에 기온·습도·풍속이 없습니다") from None


# ---------- 캐시 ----------

_cache: dict[tuple[int, int], tuple[float, Observation]] = {}
_lock = threading.Lock()


def get_weather(lat: float, lon: float) -> tuple[Observation, bool]:
    """(관측값, stale). stale=True면 기상청 오류로 이전 값을 돌려준 것."""
    key = latlon_to_grid(lat, lon)
    with _lock:
        cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < settings.WEATHER_CACHE_SEC:
        return cached[1], False

    try:
        obs = fetch_nowcast(*key)
    except WeatherUnavailable:
        if cached:
            return cached[1], True
        raise

    with _lock:
        _cache[key] = (time.monotonic(), obs)
    return obs, False


def clear_cache() -> None:
    with _lock:
        _cache.clear()
