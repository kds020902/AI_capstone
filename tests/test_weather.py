"""귀가 전 가장 추운 때 찾기 (네트워크 없이 시간별 자료로만)."""
from core.weather import coldest_until


def _weather(now, start_day="2026-10-06", hours=48, temps=None):
    times = [f"{start_day}T{h % 24:02d}:00" if h < 24 else f"2026-10-07T{h - 24:02d}:00" for h in range(hours)]
    temps = temps or [15.0] * hours
    return {"time": now, "hourly": {"time": times, "temperature": temps, "apparent_temperature": [t - 1 for t in temps]}}


def test_picks_coldest_hour_between_now_and_return():
    temps = [10.0] * 48
    temps[13], temps[19], temps[23] = 20.0, 9.0, 7.0     # 13시 20℃, 19시 9℃, 23시 7℃
    w = _weather("2026-10-06T11:15", temps=temps)
    assert coldest_until(w, 20)["label"] == "19시"           # 20시까지만 보므로 23시는 제외
    later = coldest_until(w, 24)
    assert later["label"] == "23시" and later["temperature"] == 7.0 and later["apparent_temperature"] == 6.0


def test_ignores_past_hours_and_handles_midnight():
    temps = [10.0] * 48
    temps[6] = 2.0                                           # 오늘 새벽 6시 (이미 지남)
    temps[24] = 5.0                                          # 내일 0시 = 자정
    w = _weather("2026-10-06T11:15", temps=temps)
    later = coldest_until(w, 24)
    assert later["label"] == "자정" and later["temperature"] == 5.0


def test_no_hourly_data_or_already_past():
    assert coldest_until({"time": "2026-10-06T11:15"}, 22) is None
    assert coldest_until(_weather("2026-10-06T22:30"), 20) is None   # 귀가 시각이 이미 지남


def test_retries_once_after_timeout(monkeypatch):
    import requests
    from core import weather

    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"current": {"time": "2026-10-06T11:15", "temperature_2m": 17.0, "relative_humidity_2m": 60,
                                "apparent_temperature": 16.0, "precipitation": 0, "rain": 0, "wind_speed_10m": 5},
                    "hourly": {"time": ["2026-10-06T21:00"], "temperature_2m": [10.0], "apparent_temperature": [8.0]}}

    def fake_get(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.Timeout()
        return Resp()

    monkeypatch.setattr(weather.requests, "get", fake_get)
    w = weather.get_current_weather(37.4, 127.0)
    assert w["ok"] and len(calls) == 2
    assert weather.coldest_until(w, 22)["apparent_temperature"] == 8.0
