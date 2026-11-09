"""Streamlit Secrets 파일 읽기: 파일 없음은 로컬 모드, 형식 오류는 멈춤 — 새 Streamlit(1.66)과 옛 Streamlit(1.40, Python 3.10
로컬에서 설치될 수 있는 하한)에서 같은 결과인지. 옛 버전은 읽기 실패를 화면에 st.error 로 먼저 띄우므로(설정값이 섞일 수
있음) 그 출력이 생기지 않는지도 본다. 시험 값은 더미이고 실제 비밀값을 쓰지 않는다."""
import pytest

import streamlit
from core import config

DUMMY = "dummy-secret-value-123"


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    """이 시험만의 secrets.toml 경로와 새 Secrets 객체를 쓰고, st.error 로 화면에 나간 문구를 모은다."""
    path = tmp_path / ".streamlit" / "secrets.toml"
    shown = []
    real_get = streamlit.config.get_option
    monkeypatch.setattr(streamlit.config, "get_option",
                        lambda key, *a, **k: [str(path)] if key == "secrets.files" else real_get(key, *a, **k))
    monkeypatch.setattr(streamlit, "secrets", type(streamlit.secrets)())
    monkeypatch.setattr(streamlit, "error", lambda body, *a, **k: shown.append(str(body)))
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)

    def write(text):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return write, shown


def test_no_secrets_file_is_local_mode_without_error_on_screen(secrets_file):
    _, shown = secrets_file
    assert config.get_setting("SUPABASE_URL") is None and config.cloud_settings() is None
    assert shown == []            # 옛 버전은 여기서 'No secrets found' 를 화면에 띄웠음


@pytest.mark.parametrize("text", [
    f'SUPABASE_URL = "https://abc.supabase.co"\nSUPABASE_KEY = {DUMMY}\n',               # 따옴표 빠짐 (일반 형식 오류)
    f'["No secrets"]\nTOKEN = "{DUMMY}"\n["No secrets"]\n',                             # 오류 본문에 'No secrets' + 값
])
def test_broken_secrets_file_stops_without_showing_values(secrets_file, text):
    write, shown = secrets_file
    write(text)
    with pytest.raises(config.SettingsError) as e:
        config.cloud_settings()
    assert DUMMY not in str(e.value) and "abc.supabase.co" not in str(e.value) and "형식 오류" in str(e.value)
    assert not any(DUMMY in s for s in shown)                 # 화면에 먼저 나간 오류에도 값이 없음
    assert shown == []


def test_good_secrets_file_is_read(secrets_file):
    write, shown = secrets_file
    write('SUPABASE_URL = "https://abc.supabase.co"\nSUPABASE_KEY = "sb_publishable_dummy"\n')
    assert config.cloud_settings() == {"url": "https://abc.supabase.co", "key": "sb_publishable_dummy"}
    assert shown == []


class _Raising:
    """st.secrets 대신: 읽으면 주어진 오류를 낸다. 옛 버전처럼 출력 끄기 함수가 있는지도 고를 수 있다."""

    def __init__(self, error, old_style=False):
        self.error, self.calls = error, []
        if old_style:
            self._suppress_print_error_on_exception = False
            self.set_suppress_print_error_on_exception = lambda v: (self.calls.append(v),
                                                                   setattr(self, "_suppress_print_error_on_exception", v))

    def __contains__(self, name):
        raise self.error


class _WithId(FileNotFoundError):
    def __init__(self, message, error_id):
        super().__init__(message)
        self.error_id = error_id


@pytest.mark.parametrize("error, is_missing", [
    (_WithId("No secrets found. Valid paths: x", "no-secrets-found"), True),
    (_WithId("Error parsing secrets file: Cannot declare ('No secrets',) twice", "failed-parsing-secrets-file"), False),
    (_WithId("No secrets found. (형식 오류인데 문구가 겹침)", "failed-parsing-secrets-file"), False),   # error_id 가 우선
    (FileNotFoundError("No secrets found. Valid paths for a secrets.toml file: x"), True),         # 1.40
    (FileNotFoundError("No secrets files found. Valid paths for a secrets.toml file are: x"), True),  # 그 전
    (ValueError(f"What? No secrets already exists?{{'TOKEN': '{DUMMY}'}} (line 3 column 1 char 40)"), False),  # 옛 toml
    (FileNotFoundError("Error parsing secrets file: No secrets ..."), False),                      # 문구가 그 말로 시작 안 함
])
def test_missing_file_is_told_apart_strictly(monkeypatch, error, is_missing):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    fake = _Raising(error, old_style=True)
    monkeypatch.setattr(streamlit, "secrets", fake)
    if is_missing:
        assert config.get_setting("SUPABASE_URL") is None
    else:
        with pytest.raises(config.SettingsError) as e:
            config.get_setting("SUPABASE_URL")
        assert DUMMY not in str(e.value)
    assert fake.calls == [True, False] and fake._suppress_print_error_on_exception is False   # 끈 뒤 원래대로
