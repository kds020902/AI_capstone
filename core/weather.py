from datetime import date, timedelta

import requests

CITY_PRESETS = {
    "인천": (37.4563, 126.7052),
    "안양": (37.3943, 126.9568),
    "서울": (37.5665, 126.9780),
    "수원": (37.2636, 127.0286),
    "대전": (36.3504, 127.3845),
    "대구": (35.8714, 128.6014),
    "광주": (35.1595, 126.8526),
    "부산": (35.1796, 129.0756),
}

RETURN_HOURS = [18, 20, 22, 24]  # 귀가 예정 시각 선택지 (24 = 자정)


def get_current_weather(latitude, longitude):
    """현재 날씨 + 오늘·내일 시간별 기온(귀가 전 가장 추운 때를 찾는 데 씀)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,wind_speed_10m",
        "hourly": "temperature_2m,apparent_temperature",
        "forecast_days": 2,
        "timezone": "auto",
    }
    try:
        for attempt in range(2):  # 가끔 응답이 멈추는 경우가 있어 한 번 더 시도
            try:
                r = requests.get(url, params=params, timeout=6)
                break
            except (requests.Timeout, requests.ConnectionError):
                if attempt:
                    raise
        r.raise_for_status()
        body = r.json()
        cur, hourly = body["current"], body.get("hourly") or {}
        return {
            "ok": True,
            "time": cur["time"],
            "temperature": float(cur["temperature_2m"]),
            "humidity": float(cur["relative_humidity_2m"]),
            "apparent_temperature": float(cur["apparent_temperature"]),
            "precipitation": float(cur["precipitation"]),
            "rain": float(cur.get("rain", 0)),
            "wind_speed": float(cur["wind_speed_10m"]),
            "hourly": {"time": hourly.get("time", []), "temperature": hourly.get("temperature_2m", []),
                       "apparent_temperature": hourly.get("apparent_temperature", [])},
            "source": "Open-Meteo 현재 날씨",
        }
    except Exception as e:
        return {"ok": False, "message": f"자동 조회 실패. 수동 입력을 사용하세요. ({type(e).__name__})"}


def coldest_until(weather, return_hour):
    """지금 이후 ~ 귀가 시각(오늘 return_hour시, 24 = 자정) 중 체감온도가 가장 낮은 시각.
    반환: {"time", "temperature", "apparent_temperature", "label": "21시"} 또는 None (시간별 자료가 없거나 이미 지남)
    시각은 모두 'YYYY-MM-DDTHH:MM' 현지 시각 문자열이라 문자열 비교로 순서를 정한다."""
    now = weather.get("time") or ""
    h = weather.get("hourly") or {}
    if len(now) < 16 or not h.get("time"):
        return None
    day = date.fromisoformat(now[:10]) + timedelta(days=return_hour // 24)
    until = f"{day.isoformat()}T{return_hour % 24:02d}:00"
    best = None
    for t, temp, app in zip(h["time"], h["temperature"], h["apparent_temperature"]):
        if now < t <= until and temp is not None and app is not None and (best is None or app < best["apparent_temperature"]):
            best = {"time": t, "temperature": float(temp), "apparent_temperature": float(app)}
    if best:
        hour = int(best["time"][11:13])
        best["label"] = "자정" if hour == 0 else f"{hour}시"
    return best
