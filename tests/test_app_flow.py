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


def _photo(i):
    """서로 다른 작은 옷 사진 대신 쓸 단색 JPEG (색이 달라 같은 사진으로 보지 않는다)."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 80), (12 * i, 255 - 12 * i, 90)).save(buf, "JPEG")
    return (f"p{i}.jpg", buf.getvalue(), "image/jpeg")


def test_cloud_mode_direct_add_many_photos(cloud):
    """직접 추가: 12장 → 10장만, 확인한 2장만 한 번에 저장 (사진은 내 폴더에 겹치지 않는 이름으로), 같은 사진은 한 번만."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "d@example.com")
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input("D")
    _button(at, "저장").click().run()
    _goto(at, "내 옷장")
    assert [t.label for t in at.tabs] == ["옷장 목록", "30개 스타터 옷장", "직접 추가"]
    up = next(u for u in at.file_uploader if u.label == "저장할 의류를 넣어 주세요 (최대 10장까지)")
    up.set_value([_photo(i) for i in range(12)]).run()
    assert not at.exception
    assert any("2장은 넣지 않았어요" in t.value for t in at.toast)
    assert len(at.session_state.ba_cards) == 10
    assert _button(at, "저장 (0장)").disabled
    _button(at, "✓ 확인하고 다음 ›").click().run()
    _button(at, "›").click().run()
    _button(at, "✓ 확인하고 다음 ›").click().run()
    _button(at, "저장 (2장)").click().run()
    assert not at.exception
    rows = cloud.rows("wardrobe_items")
    assert len(rows) == 2 and len(at.session_state.ba_cards) == 8
    paths = [r["image_path"] for r in rows]
    uid = rows[0]["user_id"]
    assert all(p.startswith(f"sb:{uid}/b") for p in paths) and len(set(paths)) == 2
    assert all(f"wardrobe/{p[3:]}" in cloud.objects for p in paths)
    assert len({r["name"] for r in rows}) == 2                       # 자동 이름이 겹치지 않는다
    # 다시 올리기: 남은 사진과 같은 사진은 한 번만
    _button(at, "다시 올리기").click().run()
    more = next(u for u in at.file_uploader if u.label.startswith("사진 더 넣기 (지금 8장"))
    more.set_value([_photo(5), _photo(20)]).run()
    assert any("같은 사진 1장은 한 번만" in t.value for t in at.toast)
    assert len(at.session_state.ba_cards) == 9
    assert not at.exception


def _local_app(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    return AppTest.from_file(APP, default_timeout=TIMEOUT).run()


def test_wardrobe_change_clears_old_recommendation(tmp_path, monkeypatch):
    """추천을 받은 뒤 옷장에서 옷을 비활성화하면, 그 옷이 든 지난 추천이 남아 있지 않다."""
    at = _local_app(tmp_path, monkeypatch)
    _make_profile_recommend_and_save(at, "옷장변경")
    _goto(at, "오늘의 추천")
    assert at.session_state["recommendations"]
    _goto(at, "내 옷장")
    next(b for b in at.button if b.label == "비활성화").click().run()
    assert at.session_state["recommendations"] is None
    _goto(at, "오늘의 추천")
    assert not any("1위" in m.value for m in at.markdown)
    assert not at.exception


def test_weather_is_kept_per_city(tmp_path, monkeypatch):
    """한 도시 날씨를 불러온 뒤 도시를 바꾸면, 앞 도시 날씨로 추천하지 않고 다시 불러오라고 안내한다."""
    from core import weather

    def fake_weather(lat, lon):
        return {"ok": True, "temperature": 5.0, "humidity": 50.0, "apparent_temperature": 3.0, "precipitation": 0.0,
                "rain": 0.0, "wind_speed": 4.0, "source": "fake"}

    monkeypatch.setattr(weather, "get_current_weather", fake_weather)
    at = _local_app(tmp_path, monkeypatch)
    _make_profile_recommend_and_save(at, "날씨")
    _goto(at, "오늘의 추천")
    _by_label(at.radio, "날씨").set_value("자동 조회").run()
    _by_label(at.selectbox, "도시").set_value("서울").run()
    _button(at, "현재 날씨 불러오기").click().run()
    assert _by_label(at.metric, "기온").value == "5.0℃"
    _by_label(at.selectbox, "도시").set_value("수원").run()
    assert not [m for m in at.metric if m.label == "기온"]
    assert any("서울 날씨를 불러온 상태" in c.value for c in at.caption)
    _by_label(at.selectbox, "도시").set_value("서울").run()
    assert _by_label(at.metric, "기온").value == "5.0℃"
    assert not at.exception
