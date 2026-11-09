"""실행 설정 읽기: 환경 변수 → Streamlit secrets 순서로 찾는다.

공개 배포(Streamlit Community Cloud)에서는 앱 설정의 Secrets 에 아래 이름을 넣는다.
  SUPABASE_URL      Supabase 프로젝트 주소 (https://xxxx.supabase.co)
  SUPABASE_KEY      공개용 publishable 키 (sb_publishable_…). service_role/secret 키는 절대 넣지 않는다.
                    예전 anon public 키(eyJ…)도 아직 동작하지만 Supabase가 2026년 말까지 없앨 예정이라 바꿔 둔다
  TRYON_SPACE_URL   (선택) 가상 착용 Hugging Face Space 페이지 주소 (https://huggingface.co/spaces/아이디/공간이름)
  WEB_TRIAL_URL     (선택) 웹 체험판 주소 (GitHub Pages 등)
SUPABASE_URL·SUPABASE_KEY 가 둘 다 없으면 지금처럼 로컬 SQLite + data/uploads 로 동작한다.
하나만 있거나 Secrets 파일 형식이 틀렸으면 로컬 모드로 넘어가지 않고 멈춘다 (공개 주소에서 로그인 없는 공용
SQLite 로 돌아가면 방문자끼리 데이터가 섞이고 서버가 다시 켜질 때 사라진다).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re


class SettingsError(ValueError):
    """설정이 반쪽이거나 Secrets 파일을 읽을 수 없을 때. 문구에 설정값(비밀값)은 넣지 않는다."""


class UnsafeKeyError(SettingsError):
    """서버 전용 비밀 키를 앱에 넣었을 때."""


LEGACY_KEY_WARNING = (
    "SUPABASE_KEY 에 예전 anon public 키(eyJ…)가 들어 있어요. Supabase가 2026년 말까지 예전 키를 없앨 예정이니 "
    "대시보드 Project Settings → API Keys 의 publishable 키(sb_publishable_…)로 바꿔 주세요 (DEPLOY.md 1-4)."
)


# 옛 Streamlit 이 Secrets 파일이 없을 때 내는 FileNotFoundError 문구의 시작 (1.40: "No secrets found.", 그 전: "No secrets files found.")
NO_SECRETS_PREFIXES = ("No secrets found.", "No secrets files found.")


def _no_secrets_file(e):
    """Streamlit 이 'Secrets 파일이 아예 없음'으로 낸 오류인지. 새 버전은 error_id 로만 판단하고(형식 오류도 같은 예외 종류),
    error_id 가 없는 옛 버전은 FileNotFoundError 이면서 알려진 문구로 시작할 때만 (형식 오류 본문에 'No secrets' 가 있어도 아님)."""
    error_id = getattr(e, "error_id", None)
    if error_id is not None:
        return error_id == "no-secrets-found"
    return isinstance(e, FileNotFoundError) and str(e).startswith(NO_SECRETS_PREFIXES)


def get_setting(name, default=None):
    value = os.environ.get(name)
    if value:
        return value.strip()
    try:
        import streamlit as st
    except ImportError:              # Streamlit 없이 도는 스크립트
        return default
    secrets = st.secrets
    # 옛 Streamlit(1.40 등)은 Secrets 를 읽다 실패하면 예외를 던지기 전에 오류 원문(이미 읽은 설정값이 섞일 수 있음)을
    # 화면에 st.error 로 먼저 띄운다. 그 버전에만 있는 공개 함수로 읽는 동안만 끄고, 끝나면 원래대로 돌린다.
    quiet = getattr(secrets, "set_suppress_print_error_on_exception", None)
    before = bool(getattr(secrets, "_suppress_print_error_on_exception", False))
    try:
        if quiet:
            quiet(True)
        if name in secrets:
            v = secrets[name]
            return str(v).strip() if v is not None else default
    except Exception as e:
        if _no_secrets_file(e):      # secrets.toml 이 없음 = 설정 안 함 (로컬 실행)
            return default
        where = re.search(r"line \d+(?:,? column \d+)?", str(e))   # 파일 내용(비밀값)은 문구에 넣지 않는다
        raise SettingsError("Streamlit Secrets 설정을 읽지 못했어요 (형식 오류"
                            + (f", {where.group(0)}" if where else "") + "). "
                            "secrets.toml 또는 앱 Settings → Secrets 의 형식을 확인해 주세요 (예: NAME = \"값\").") from None
    finally:
        if quiet:
            quiet(before)
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
            "Supabase 대시보드 Project Settings → API Keys 의 publishable 키(sb_publishable_…)로 바꿔 주세요."
        )
    return key


def is_legacy_key(key):
    """예전 anon public 키(JWT)인지. 동작은 하지만 Supabase가 2026년 말까지 없앨 예정이다."""
    return _jwt_role(key) == "anon"


_DASHBOARD = re.compile(r"^https?://(?:app\.)?supabase\.com/dashboard/project/([a-z0-9]+)", re.I)


def normalize_supabase_url(url):
    """대시보드 주소(https://supabase.com/dashboard/project/<id>/...)나 API 경로가 붙은 주소를
    프로젝트 주소 https://<id>.supabase.co 로 바꾼다. 흔한 붙여 넣기 실수를 받아 준다."""
    url = url.strip().rstrip("/")
    m = _DASHBOARD.match(url)
    if m:
        return f"https://{m.group(1).lower()}.supabase.co"
    return re.sub(r"/(?:rest|auth|storage)/v1(?:/.*)?$", "", url)


def cloud_settings():
    """{"url", "key"} 또는 None(로컬 모드: 둘 다 없을 때만). 하나만 있으면 SettingsError."""
    url, key = get_setting("SUPABASE_URL"), get_setting("SUPABASE_KEY")
    if not url and not key:
        return None
    if not url or not key:
        missing, has = ("SUPABASE_URL", "SUPABASE_KEY") if not url else ("SUPABASE_KEY", "SUPABASE_URL")
        raise SettingsError(f"{has} 는 있는데 {missing} 가 없어요. 공개 배포는 둘 다 넣고, "
                            "로그인 없는 로컬 모드로 쓰려면 둘 다 지워 주세요.")
    key = check_public_key(key)
    if is_legacy_key(key):    # 사용자 화면이 아니라 서버 기록(Streamlit Cloud → Manage app → Logs)에만 남긴다
        logging.getLogger(__name__).warning(LEGACY_KEY_WARNING)
    return {"url": normalize_supabase_url(url), "key": key}
