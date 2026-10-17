"""공개 배포 모드의 로그인 (Supabase Auth, 이메일 + 비밀번호).

- 브라우저 세션(Streamlit 세션)마다 클라이언트를 하나씩 만든다. 사용자 토큰이 섞이지 않도록
  st.cache_resource 처럼 여러 사람이 공유하는 캐시에는 절대 넣지 않는다.
- 비밀번호는 Supabase Auth 로만 보내고 앱 DB나 로그에 남기지 않는다.
"""
from __future__ import annotations


def make_client(settings):
    """테스트에서 바꿔 끼울 수 있게 함수로 둔다."""
    from supabase import ClientOptions, create_client
    return create_client(settings["url"], settings["key"],
                         options=ClientOptions(auto_refresh_token=True, persist_session=True))


def _msg(e):
    text = str(getattr(e, "message", "") or e)
    low = text.lower()
    if "invalid login credentials" in low:
        return "이메일 또는 비밀번호가 맞지 않아요."
    if "email not confirmed" in low:
        return "이메일 인증이 아직 안 됐어요. 받은 메일의 링크를 누른 뒤 다시 로그인해 주세요."
    if "already registered" in low or "already exists" in low:
        return "이미 가입된 이메일이에요. 로그인해 주세요."
    if "password" in low and ("at least" in low or "short" in low or "weak" in low):
        return "비밀번호가 너무 짧거나 쉬워요. 6자 이상으로 정해 주세요."
    if "rate limit" in low or "too many" in low:
        return "요청이 너무 많아요. 잠시 뒤 다시 시도해 주세요."
    if "email address not authorized" in low:
        return "지금은 이메일 인증 메일을 보낼 수 없는 설정이에요. 관리자에게 알려 주세요."
    return f"처리하지 못했어요: {text}"


def sign_up(client, email, password):
    """반환 (user 또는 None, 안내 문구). 이메일 인증을 켠 프로젝트면 세션 없이 user 만 온다."""
    try:
        res = client.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        return None, _msg(e)
    if res.session is None:
        return None, "가입 신청이 됐어요. 받은 메일의 인증 링크를 누른 뒤 로그인해 주세요."
    return res.user, "가입하고 로그인했어요."


def sign_in(client, email, password):
    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        return None, _msg(e)
    return res.user, "로그인했어요."


def current_user(client):
    """세션이 살아 있으면 user, 아니면 None. 만료된 토큰은 여기서 새로 고친다."""
    try:
        session = client.auth.get_session()
    except Exception:
        return None
    return session.user if session else None


def sign_out(client):
    try:
        client.auth.sign_out()
    except Exception:
        pass
