"""날씨 API 테스트. 기상청은 호출하지 않고 가짜 응답으로 대체합니다."""

from datetime import datetime

import pytest

from app import weather
from app.config import settings
from tests.test_api import client  # noqa: F401  (같은 테스트 DB·구조물 사용)

OBS = weather.Observation(
    temperature_c=7.2, humidity_pct=23, wind_speed_ms=5.1, wind_dir_deg=318,
    observed_at=datetime(2026, 11, 6, 20, 0, tzinfo=weather.KST),
)


@pytest.fixture(autouse=True)
def _clean_cache():
    weather.clear_cache()
    yield
    weather.clear_cache()


def test_grid_conversion():
    assert weather.latlon_to_grid(37.5665, 126.9780) == (60, 127)  # 서울시청 (기상청 기준값)
    assert weather.latlon_to_grid(37.8014, 128.9088) == (93, 133)  # 스카이베이 경포


@pytest.mark.parametrize("ms, level", [
    (0, "weak"), (3.9, "weak"), (4, "moderate"), (8.9, "moderate"),
    (9, "strong"), (13.9, "strong"), (14, "very_strong"),
])
def test_wind_level(ms, level):
    assert weather.wind_level(ms) == level


def test_weather_endpoint_and_cache(client, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(weather, "fetch_nowcast", lambda nx, ny: calls.append((nx, ny)) or OBS)

    body = client.get("/structures/1/weather").json()
    assert body == {
        "structureId": 1, "temperatureC": 7.2, "humidityPct": 23, "windSpeedMs": 5.1,
        "windDirDeg": 318, "windLevel": "moderate", "observedAt": "2026-11-06T20:00:00+09:00",
        "source": "KMA", "stale": False,
    }
    client.get("/structures/1/weather")
    assert calls == [(93, 133)]  # 두 번째는 캐시


def test_stale_when_kma_fails_after_success(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(weather, "fetch_nowcast", lambda nx, ny: OBS)
    client.get("/structures/1/weather")

    def boom(nx, ny):
        raise weather.WeatherUnavailable("기상청 오류")
    monkeypatch.setattr(weather, "fetch_nowcast", boom)
    monkeypatch.setattr(settings, "WEATHER_CACHE_SEC", 0)  # 캐시 만료
    body = client.get("/structures/1/weather").json()
    assert body["stale"] is True and body["temperatureC"] == 7.2


def test_503_without_any_data(client, monkeypatch):  # noqa: F811
    def boom(nx, ny):
        raise weather.WeatherUnavailable("기상청 오류")
    monkeypatch.setattr(weather, "fetch_nowcast", boom)
    r = client.get("/structures/1/weather")
    assert r.status_code == 503 and "기상청 오류" in r.json()["detail"]


def test_404(client):  # noqa: F811
    assert client.get("/structures/2/weather").status_code == 404  # 위경도 없음
    assert client.get("/structures/999/weather").status_code == 404


def test_structure_response_has_location(client):  # noqa: F811
    s = client.get("/structures/1").json()
    assert (s["latitude"], s["longitude"]) == (37.8014, 128.9088)


# ---------- fetch_nowcast: 기상청 응답 처리 ----------

class FakeResp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text = payload, text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _kma(code, items=None):
    body = {"items": {"item": items}} if items else {}
    return {"response": {"header": {"resultCode": code, "resultMsg": "MSG"}, "body": body}}


ITEMS = [
    {"baseDate": "20261106", "baseTime": "1900", "category": c, "obsrValue": v}
    for c, v in [("T1H", "7.2"), ("REH", "23"), ("WSD", "1.3"), ("VEC", "318"), ("PTY", "0")]
]
NOW = datetime(2026, 11, 6, 20, 5, tzinfo=weather.KST)


def test_fetch_falls_back_to_previous_hour(monkeypatch):
    monkeypatch.setattr(settings, "KMA_SERVICE_KEY", "SECRET")
    seen = []

    def fake_get(url, params, timeout):
        seen.append(params["base_time"])
        return FakeResp(_kma("03") if params["base_time"] == "2000" else _kma("00", ITEMS))
    monkeypatch.setattr(weather.httpx, "get", fake_get)

    obs = weather.fetch_nowcast(93, 133, now=NOW)
    assert seen == ["2000", "1900"]
    assert (obs.temperature_c, obs.humidity_pct, obs.wind_speed_ms) == (7.2, 23, 1.3)
    assert obs.observed_at.hour == 19


def test_fetch_errors_do_not_leak_key(monkeypatch):
    monkeypatch.setattr(settings, "KMA_SERVICE_KEY", "SECRET")
    # 키 오류는 XML로 옴
    monkeypatch.setattr(weather.httpx, "get", lambda url, params, timeout: FakeResp(
        text="<OpenAPI_ServiceResponse>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</OpenAPI_ServiceResponse>"))
    with pytest.raises(weather.WeatherUnavailable) as e:
        weather.fetch_nowcast(93, 133, now=NOW)
    assert "SECRET" not in str(e.value)

    monkeypatch.setattr(weather.httpx, "get", lambda url, params, timeout: FakeResp(_kma("30")))
    with pytest.raises(weather.WeatherUnavailable, match="30"):
        weather.fetch_nowcast(93, 133, now=NOW)


def test_fetch_without_key(monkeypatch):
    monkeypatch.setattr(settings, "KMA_SERVICE_KEY", "")
    with pytest.raises(weather.WeatherUnavailable, match="KMA_SERVICE_KEY"):
        weather.fetch_nowcast(93, 133, now=NOW)
