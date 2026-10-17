"""실행 설정 읽기: 환경 변수 → Streamlit secrets 순서로 찾는다.

공개 배포(Streamlit Community Cloud)에서는 앱 설정의 Secrets 에 아래 이름을 넣는다.
  SUPABASE_URL      Supabase 프로젝트 주소 (https://xxxx.supabase.co)
  SUPABASE_KEY      공개용 키 (anon public 또는 publishable 키). service_role/secret 키는 절대 넣지 않는다
  TRYON_SPACE_URL   (선택) 가상 착용 Hugging Face Space 페이지 주소 (https://huggingface.co/spaces/아이디/공간이름)
  WEB_TRIAL_URL     (선택) 웹 체험판 주소 (GitHub Pages 등)
SUPABASE_URL·SUPABASE_KEY 가 없으면 지금처럼 로컬 SQLite + data/uploads 로 동작한다.
"""
from __future__ import annotations

import base64
import json
import os


class UnsafeKeyError(ValueError):
    """서버 전용 비밀 키를 앱에 넣었을 때."""


def get_setting(name, default=None):
    value = os.environ.get(name)
    if value:
        return value.strip()
    try:
        import streamlit as st
        if name in st.secrets:
            v = st.secrets[name]
            return str(v).strip() if v is not None else default
    except Exception:  # secrets.toml 이 없거나 Streamlit 밖에서 실행
        pass
    return default


def _jwt_role(key):
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except Exception:
        return None


def check_public_key(key):
    """브라우저에 노출돼도 되는 공개 키인지 확인. 서비스 키면 RLS(사용자별 접근 제한)를 건너뛰므로 거부한다."""
    if key.startswith("sb_secret_") or _jwt_role(key) == "service_role":
        raise UnsafeKeyError(
            "SUPABASE_KEY 에 service_role/secret 키가 들어 있어요. "
            "Supabase 대시보드의 anon public(또는 publishable) 키로 바꿔 주세요."
        )
    return key


def cloud_settings():
    """{"url", "key"} 또는 None(로컬 모드)."""
    url, key = get_setting("SUPABASE_URL"), get_setting("SUPABASE_KEY")
    if not url or not key:
        return None
    return {"url": url.rstrip("/"), "key": check_public_key(key)}
