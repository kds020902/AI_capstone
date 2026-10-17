"""앱 화면 흐름 (Streamlit AppTest). 로컬 모드와 공개 배포 모드(가짜 Supabase)를 둘 다 끝까지 눌러 본다."""
import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from core import cloud_auth, db  # noqa: E402
from tests.fake_supabase import FakeBackend, FakeClient  # noqa: E402

TIMEOUT = 120
APP = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app.py")


def _by_label(widgets, label):
    return next(w for w in widgets if w.label == label)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _goto(at, page):
    at.sidebar.radio[0].set_value(page).run()


def _make_profile_recommend_and_save(at, name):
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input(name)
    _button(at, "저장" if any(b.label == "저장" for b in at.button) else "사용자 생성").click().run()
    _goto(at, "내 옷장")
    _button(at, "프로필 성별 기준 30개 추가").click().run()
    _goto(at, "오늘의 추천")
    _by_label(at.radio, "날씨").set_value("수동 입력").run()
    _button(at, "TOP 3 추천 생성").click().run()
    assert any("1위" in m.value for m in at.markdown), "추천이 안 나옴"
    next(b for b in at.button if b.label == "이 코디 저장").click().run()
    assert any("코디를 저장했습니다" in s.value for s in at.success)
    _goto(at, "KPI·데이터")
    metric = _by_label(at.metric, "추천 세션")
    assert metric.value == "1"


def test_local_mode_full_flow(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert at.sidebar.radio[0].label == "메뉴"          # 로그인 없이 바로 시작 (예전과 같음)
    _make_profile_recommend_and_save(at, "로컬사용자")
    assert not at.exception


@pytest.fixture
def cloud(monkeypatch):
    backend = FakeBackend()
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_test")
    monkeypatch.setenv("TRYON_SPACE_URL", "https://huggingface.co/spaces/me/closet-tryon")
    monkeypatch.setattr(cloud_auth, "make_client", lambda settings: FakeClient(backend))
    return backend


def _signup(at, email, pw="secret123"):
    at.text_input(key="signup_email").input(email)
    at.text_input(key="signup_pw").input(pw)
    at.text_input(key="signup_pw2").input(pw)
    _button(at, "가입하기").click().run()


def test_cloud_mode_requires_login_and_keeps_accounts_apart(cloud):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["로그인", "회원가입"]
    assert len(at.sidebar.radio) == 0                     # 로그인 전에는 메뉴가 없다

    # 틀린 비밀번호
    at.text_input(key="login_email").input("nobody@example.com")
    at.text_input(key="login_pw").input("wrongpass")
    _button(at, "로그인").click().run()
    assert any("맞지 않아요" in e.value for e in at.error)

    _signup(at, "a@example.com")
    assert at.sidebar.radio[0].label == "메뉴"
    assert any("a@example.com" in c.value for c in at.sidebar.caption)
    _make_profile_recommend_and_save(at, "A")
    _goto(at, "오늘의 추천")
    assert any(b.label == "👗 가상 착용(예시)" for b in at.button)   # 설정된 Space 가 있으면 버튼이 보인다
    _button(at, "👗 가상 착용(예시)").click().run()
    assert not at.exception
    frame, link = at.get("iframe")[0].proto, at.get("link_button")[0].proto
    assert frame.src.startswith("https://me-closet-tryon.hf.space/?")            # 앱 안 창
    assert link.url.startswith("https://huggingface.co/spaces/me/closet-tryon?")  # 새 창 (Hugging Face 로그인 적용)
    assert "kind=upper" in frame.src and "kind2=lower" in frame.src
    assert at.get("multiselect")[0].proto.default == [0, 1]                      # 상의 + 하의 기본 선택

    # 다른 브라우저(세션)에서 B가 가입하면 A의 옷장·기록이 안 보인다
    bt = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(bt, "b@example.com")
    _goto(bt, "프로필")
    _by_label(bt.text_input, "이름 / 닉네임").input("B")
    _button(bt, "저장").click().run()
    _goto(bt, "내 옷장")
    assert any("등록된 의류가 없습니다" in i.value for i in bt.info)
    _goto(bt, "KPI·데이터")
    assert _by_label(bt.metric, "추천 세션").value == "0"
    assert len(cloud.rows("wardrobe_items")) == 30 and len(cloud.rows("profiles")) == 2

    # 로그아웃하면 다시 로그인 화면
    _button(at, "로그아웃").click().run()
    assert [t.label for t in at.tabs] == ["로그인", "회원가입"]
    assert not at.exception and not bt.exception


def test_cloud_mode_signup_with_email_confirmation(cloud):
    cloud.autoconfirm = False
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "c@example.com")
    assert any("인증 링크" in i.value for i in at.info)
    assert len(at.sidebar.radio) == 0
