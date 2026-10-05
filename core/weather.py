
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

def get_current_weather(latitude, longitude):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,wind_speed_10m",
        "timezone": "auto",
    }
    try:
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        cur = r.json()["current"]
        return {
            "ok": True,
            "temperature": float(cur["temperature_2m"]),
            "humidity": float(cur["relative_humidity_2m"]),
            "apparent_temperature": float(cur["apparent_temperature"]),
            "precipitation": float(cur["precipitation"]),
            "rain": float(cur.get("rain", 0)),
            "wind_speed": float(cur["wind_speed_10m"]),
            "source": "Open-Meteo current weather (MVP demo)",
        }
    except Exception as e:
        return {"ok": False, "message": f"자동 조회 실패. 수동 입력을 사용하세요. ({type(e).__name__})"}
