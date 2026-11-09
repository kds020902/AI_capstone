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


def test_half_settings_show_error_instead_of_local_mode(monkeypatch, tmp_path):
    """공개 배포 설정이 반쪽이면 로그인 없는 로컬 모드로 열리지 않고 무엇이 빠졌는지 보여 준다 (설정값은 안 보임)."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.sqlite3")
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert any("SUPABASE_KEY" in e.value for e in at.error)
    assert not any("example.supabase.co" in e.value for e in at.error)
    assert len(at.sidebar.radio) == 0 and not (tmp_path / "app.sqlite3").exists()   # 메뉴도 로컬 DB도 없음


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


def test_cloud_recommendation_retry_after_lost_response_keeps_one_session(cloud):
    """추천 저장이 응답 유실로 오류가 난 뒤 같은 추천을 다시 누르면, 세션이 두 번 생기지 않고 결과가 보인다.
    오류 없이 한 번 더 누르면 예전처럼 새 세션이 생긴다."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "r@example.com")
    _make_profile_recommend_and_save(at, "R")                      # 세션 1개
    _goto(at, "오늘의 추천")
    _by_label(at.radio, "날씨").set_value("수동 입력").run()
    cloud.rpc_faults["save_recommendation_session"] = "lost"
    _button(at, "TOP 3 추천 생성").click().run()
    assert at.exception                                            # 사용자는 오류를 보지만 DB에는 저장돼 있다
    assert len(cloud.rows("recommendation_sessions")) == 2
    _button(at, "TOP 3 추천 생성").click().run()
    assert not at.exception and any("1위" in m.value for m in at.markdown)
    assert len(cloud.rows("recommendation_sessions")) == 2 and len(cloud.rows("recommendations")) == 6
    sid = at.session_state["recommendation_session_id"]
    assert {r["recommendation_id"] for r in at.session_state["recommendations"]} == \
        {r["id"] for r in cloud.rows("recommendations") if r["session_id"] == sid}
    _button(at, "TOP 3 추천 생성").click().run()                   # 정상 저장 뒤 다시 누르면 새 세션 (예전과 같음)
    assert len(cloud.rows("recommendation_sessions")) == 3


def _shown_matches_db(at, cloud):
    """화면에 보이는 추천마다, 붙은 추천 id의 DB 기록이 같은 내용(옷·이유·근거·점수)인지."""
    import json
    rows = {r["id"]: r for r in cloud.rows("recommendations")}
    return all(json.loads(rows[r["recommendation_id"]]["reasons_json"]) == r["reasons"]
               and json.loads(rows[r["recommendation_id"]]["pieces_json"]) == r["pieces"]
               and json.loads(rows[r["recommendation_id"]]["components_json"]) == r["components"]
               and rows[r["recommendation_id"]]["score"] == r["score"] for r in at.session_state["recommendations"])


def test_cloud_recommendation_retry_with_changed_content_never_reuses_old_ids(cloud):
    """응답 유실 뒤 저녁 외출을 켜서 이유(저녁 안내)만 달라진 추천은 새 요청: 옛 기록 id를 새 내용에 붙이지 않는다.
    그 추천이 성공한 뒤 처음(응답 유실된) 추천을 다시 누르면, 다른 추천의 성공으로 잊지 않고 처음 요청 id를 다시 써서
    세션이 늘지 않는다. 실패한 A → 실패한 B → 다시 A 도 A의 요청 id. 정상 저장한 추천을 또 누르면 새 세션."""
    rows = lambda: cloud.rows("recommendation_sessions")                       # noqa: E731
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "rk@example.com")
    _make_profile_recommend_and_save(at, "RK")                                   # 세션 1
    _goto(at, "오늘의 추천")
    _by_label(at.radio, "날씨").set_value("수동 입력").run()
    cloud.rpc_faults["save_recommendation_session"] = "lost"
    _button(at, "TOP 3 추천 생성").click().run()                                 # P: 세션 2 (응답 유실)
    _by_label(at.checkbox, "저녁 늦게까지 밖에 있어요").check().run()
    _button(at, "TOP 3 추천 생성").click().run()                                 # 내용이 달라 새 세션 3 (성공)
    assert not at.exception and len(rows()) == 3
    assert any("저녁" in x for r in at.session_state["recommendations"] for x in r["reasons"])
    assert _shown_matches_db(at, cloud)
    _by_label(at.checkbox, "저녁 늦게까지 밖에 있어요").uncheck().run()
    _button(at, "TOP 3 추천 생성").click().run()                                 # 다시 P: 세션 2 재사용
    assert not at.exception and len(rows()) == 3
    assert at.session_state["recommendation_session_id"] == rows()[1]["id"] and _shown_matches_db(at, cloud)

    cloud.rpc_faults["save_recommendation_session"] = "lost"
    _button(at, "TOP 3 추천 생성").click().run()                                 # A(=P, 성공해서 잊음): 세션 4 (유실)
    _by_label(at.selectbox, "외출 목적").set_value("운동").run()
    cloud.rpc_faults["save_recommendation_session"] = "lost"
    _button(at, "TOP 3 추천 생성").click().run()                                 # B: 세션 5 (유실)
    _by_label(at.selectbox, "외출 목적").set_value("등교").run()
    _button(at, "TOP 3 추천 생성").click().run()                                 # 다시 A: 세션 4 재사용
    assert not at.exception and len(rows()) == 5
    assert at.session_state["recommendation_session_id"] == rows()[3]["id"] and _shown_matches_db(at, cloud)
    _button(at, "TOP 3 추천 생성").click().run()                                 # 정상 저장 뒤엔 새 세션
    assert len(rows()) == 6 and _shown_matches_db(at, cloud)


def test_recommendation_pending_requests_are_bounded(monkeypatch):
    """결과를 모르는 요청은 세션에서 REC_PENDING_MAX 개까지 기억하고, 넘치면 가장 오래된 것부터 잊는다."""
    import streamlit as st

    from screens import recommend as rec
    class _State(dict):                       # st.session_state 처럼 ss.x / ss["x"] 둘 다
        __getattr__, __setattr__ = dict.__getitem__, dict.__setitem__
    monkeypatch.setattr(rec, "REC_PENDING_MAX", 3)
    monkeypatch.setattr(st, "session_state", _State())
    ids = [rec._request_id(k) for k in "abcd"]
    assert list(st.session_state["rec_pending"]) == ["b", "c", "d"] and rec._request_id("d") == ids[3]
    assert rec._request_id("a") != ids[0]


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


def _noisy_photo(i):
    """무늬 있는 사진 (단색 사진은 JPEG를 두 번 압축해도 바이트가 같아서 두 번 압축을 못 잡는다)."""
    import io
    import random
    from PIL import Image
    buf = io.BytesIO()
    Image.frombytes("RGB", (64, 80), random.Random(i).randbytes(64 * 80 * 3)).save(buf, "JPEG")
    return (f"n{i}.jpg", buf.getvalue(), "image/jpeg")


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


def _confirm_all_and_save(at, n):
    for _ in range(n):
        _button(at, "✓ 확인하고 다음 ›").click().run()
    _button(at, f"저장 ({n}장)").click().run()


def test_cloud_direct_add_retry_does_not_duplicate_photos_or_items(cloud):
    """직접 추가 저장이 사진 올리기 중간에 실패하거나, DB 저장 뒤 응답만 유실돼도 다시 누르면 사진·옷이 늘지 않는다."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "retry@example.com")
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input("R")
    _button(at, "저장").click().run()
    _goto(at, "내 옷장")
    up = next(u for u in at.file_uploader if u.label == "저장할 의류를 넣어 주세요 (최대 10장까지)")
    up.set_value([_photo(i) for i in range(3)]).run()

    cloud.faults[("storage", "upload")] = [None, "error"]               # 1장은 올라가고 2장째에서 실패
    _confirm_all_and_save(at, 3)
    assert any("저장하지 못했어요" in e.value for e in at.error)
    assert len(cloud.objects) == 1 and cloud.rows("wardrobe_items") == [] and len(at.session_state.ba_cards) == 3
    _button(at, "저장 (3장)").click().run()                              # 확인 상태는 그대로, 다시 저장
    assert not at.exception and len(at.session_state.ba_cards) == 0
    assert len(cloud.objects) == 3 and len(cloud.rows("wardrobe_items")) == 3

    more = next(u for u in at.file_uploader if u.label == "저장할 의류를 넣어 주세요 (최대 10장까지)")
    more.set_value([_noisy_photo(7), _noisy_photo(8)]).run()
    cloud.faults[("wardrobe_items", "upsert")] = "lost"                 # DB에는 들어갔는데 응답만 유실
    _confirm_all_and_save(at, 2)
    assert any("저장하지 못했어요" in e.value for e in at.error)
    assert len(cloud.rows("wardrobe_items")) == 5 and len(at.session_state.ba_cards) == 2
    next(t for t in at.text_input if t.label == "이름").input("고친 이름").run()   # 응답 유실 뒤 카드를 고쳐서 다시 저장
    _button(at, "저장 (2장)").click().run()
    assert "고친 이름" in [r["name"] for r in cloud.rows("wardrobe_items")]       # 새로 넣지 않고 고친 내용으로
    assert not at.exception and len(at.session_state.ba_cards) == 0
    assert len(cloud.objects) == 5 and len(cloud.rows("wardrobe_items")) == 5
    assert len({r["image_path"] for r in cloud.rows("wardrobe_items")}) == 5
    from core.media import to_cloud_jpeg                       # 저장된 사진은 한 번만 압축한 것 (두 번 압축 안 함)
    assert set(cloud.objects.values()) == ({to_cloud_jpeg(_photo(i)[1]) for i in (0, 1, 2)}
                                           | {to_cloud_jpeg(_noisy_photo(i)[1]) for i in (7, 8)})


def test_cloud_candidate_retry_does_not_duplicate(cloud):
    """구매 후보 저장: 사진 올리기 실패 뒤, 그리고 저장 뒤 응답 유실 뒤 다시 눌러도 사진·후보가 하나씩만 생긴다."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "cand@example.com")
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input("C")
    _button(at, "저장").click().run()
    _goto(at, "사기 전에 맞춰보기")
    next(u for u in at.file_uploader if u.label.startswith("상품 사진")).set_value(_photo(3)).run()
    cloud.faults[("storage", "upload")] = "error"
    _button(at, "구매 후보로 저장").click().run()
    assert any("저장하지 못했어요" in e.value for e in at.error)
    assert cloud.objects == {} and cloud.rows("purchase_candidates") == []
    cloud.faults[("purchase_candidates", "upsert")] = "lost"
    _button(at, "구매 후보로 저장").click().run()
    assert any("후보 하나만 저장돼요" in e.value for e in at.error)
    assert len(cloud.objects) == 1 and len(cloud.rows("purchase_candidates")) == 1
    _button(at, "구매 후보로 저장").click().run()
    assert not at.exception
    assert len(cloud.objects) == 1 and len(cloud.rows("purchase_candidates")) == 1
    assert any(m.value == "### " + cloud.rows("purchase_candidates")[0]["name"] for m in at.markdown)


def _candidate_form(at, cloud, email):
    at.run()
    _signup(at, email)
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input("C")
    _button(at, "저장").click().run()
    _goto(at, "사기 전에 맞춰보기")


def _cand_name(at):
    return next(t for t in at.text_input if t.label == "이름")


def test_cloud_candidate_edit_after_lost_response_updates_same_candidate(cloud):
    """구매 후보 저장 응답이 유실된 뒤 이름·링크를 고쳐 다시 저장해도 후보는 하나이고 고친 내용이 남는다.
    실패한 A → 실패한 B → A 로 돌아와 저장해도 하나. 사진 없이 등록하는 후보도 같다.
    저장에 성공한 뒤 같은 사진으로 다시 저장하면 일부러 새로 등록한 것이라 두 번째 후보가 생긴다."""
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    _candidate_form(at, cloud, "ce@example.com")
    next(u for u in at.file_uploader if u.label.startswith("상품 사진")).set_value(_photo(4)).run()
    _cand_name(at).input("A 코트").run()
    cloud.faults[("purchase_candidates", "upsert")] = "lost"
    _button(at, "구매 후보로 저장").click().run()                          # A 저장됨, 응답 유실
    _cand_name(at).input("B 코트").run()
    next(t for t in at.text_input if t.label.startswith("상품 링크")).input("https://example.com/b").run()
    cloud.faults[("purchase_candidates", "update")] = "lost"            # 이미 있는 행은 고치기 단계에서 응답 유실
    _button(at, "구매 후보로 저장").click().run()                          # 같은 등록을 B 로 고침, 또 응답 유실
    assert [(c["name"], c["link"]) for c in cloud.rows("purchase_candidates")] == [("B 코트", "https://example.com/b")]
    _cand_name(at).input("A 코트").run()
    _button(at, "구매 후보로 저장").click().run()                          # 다시 A, 이번엔 성공
    assert not at.exception
    assert [c["name"] for c in cloud.rows("purchase_candidates")] == ["A 코트"] and len(cloud.objects) == 1
    _button(at, "구매 후보로 저장").click().run()                          # 같은 사진·이름으로 일부러 한 번 더
    assert len(cloud.rows("purchase_candidates")) == 2                   # 성공 뒤 같은 사진으로 새 등록 → 둘
    assert len(cloud.objects) == 2

    bt = AppTest.from_file(APP, default_timeout=TIMEOUT)                 # 사진 없는 후보
    _candidate_form(bt, cloud, "cn@example.com")
    _cand_name(bt).input("사진 없는 니트").run()
    cloud.faults[("purchase_candidates", "upsert")] = "lost"
    _button(bt, "구매 후보로 저장").click().run()
    _cand_name(bt).input("사진 없는 니트(고침)").run()
    _button(bt, "구매 후보로 저장").click().run()
    mine = [c for c in cloud.rows("purchase_candidates") if c["name"].startswith("사진 없는")]
    assert [c["name"] for c in mine] == ["사진 없는 니트(고침)"] and not bt.exception


def test_cloud_direct_add_retry_keeps_edit_made_in_wardrobe_list(cloud):
    """등록 성공(응답 유실) → 옷장 목록에서 '정보 수정' → 등록 카드 재시도: 목록에서 고친 내용이 남고, 그렇다고 알린다."""
    from core.cloud_store import CloudStore
    from tests.fake_supabase import signed_in_client
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    _signup(at, "edit@example.com")
    _goto(at, "프로필")
    _by_label(at.text_input, "이름 / 닉네임").input("E")
    _button(at, "저장").click().run()
    _goto(at, "내 옷장")
    next(u for u in at.file_uploader if u.label == "저장할 의류를 넣어 주세요 (최대 10장까지)").set_value([_photo(9)]).run()
    next(t for t in at.text_input if t.label == "이름").input("카드 셔츠").run()
    cloud.faults[("wardrobe_items", "upsert")] = "lost"
    _button(at, "저장").click().run()                                        # 저장됨, 응답 유실
    other = signed_in_client(cloud, "edit@example.com")                      # 같은 사용자의 다른 창에서 목록 수정
    store = CloudStore(other, other.uid)
    item = store.list_wardrobe_items(other.uid)[0]
    store.update_wardrobe_item(item["id"], other.uid, "목록에서 고친 이름", "상의", "셔츠", "navy", 3, 0)
    next(t for t in at.text_input if t.label == "이름").input("카드에서 고친 이름").run()
    at.button(key="ba_save").click().run()                                   # 카드 재시도 (목록의 '저장'과 구분)
    assert not at.exception and len(at.session_state.ba_cards) == 0
    assert [r["name"] for r in cloud.rows("wardrobe_items")] == ["목록에서 고친 이름"]
    assert any("목록 쪽을 그대로 뒀어요" in t.value for t in at.toast)


def test_cloud_candidate_saved_after_earlier_one_was_deleted_or_bought_is_visible(cloud):
    """후보 A 저장 응답 유실 → A 를 지우거나 사면 → 다른 후보 B 를 저장해도 B 가 목록에 보인다 (끝난 A 를 고치지 않음).
    다른 창에서 A 를 지운 경우도 저장소가 '끝난 등록'으로 알려 새 등록으로 저장한다."""
    from core.cloud_store import CloudStore
    from tests.fake_supabase import signed_in_client
    for end in ("지우기", "사기", "다른 창에서 지우기"):
        email = f"end{len(end)}@example.com"
        at = AppTest.from_file(APP, default_timeout=TIMEOUT)
        _candidate_form(at, cloud, email)
        _cand_name(at).input("A 코트").run()
        cloud.faults[("purchase_candidates", "upsert")] = "lost"
        _button(at, "구매 후보로 저장").click().run()                          # A 저장됨, 응답 유실
        at.run()                                                             # 목록에 A 가 보임
        if end == "지우기":
            _button(at, "후보에서 지우기").click().run()
        elif end == "사기":
            _button(at, "샀어요 · 옷장에 넣기").click().run()
        else:
            other = signed_in_client(cloud, email)
            st2 = CloudStore(other, other.uid)
            st2.delete_candidate(st2.list_candidates(other.uid)[0]["id"], other.uid)
        _cand_name(at).input("B 니트").run()
        _button(at, "구매 후보로 저장").click().run()
        assert not at.exception, end
        mine = [c for c in cloud.rows("purchase_candidates") if c["user_id"] == at.session_state["user_id"]]
        assert [(c["name"], c["deleted"]) for c in mine] == [("A 코트", 1), ("B 니트", 0)], end
        assert any(m.value == "### B 니트" for m in at.markdown), end


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


def test_cloud_wardrobe_rerun_reads_db_twice_and_reuses_photos(cloud, monkeypatch):
    """내 옷장을 다시 그릴 때 DB는 2번(사용자·옷장)만 읽고, 이미 받은 사진은 다시 내려받지 않는다."""
    import io

    from PIL import Image

    from core.cloud_store import CloudStore
    from core.media import SupabaseMedia
    from tests.fake_supabase import _Bucket, signed_in_client

    c = signed_in_client(cloud, "p@example.com")
    store, media = CloudStore(c, c.uid), SupabaseMedia(c, c.uid)
    store.create_user("P", "male", 0, 0)
    for i in range(3):
        buf = io.BytesIO()
        Image.new("RGB", (900, 700), (40 * i, 90, 160)).save(buf, "JPEG")
        store.create_wardrobe_item(c.uid, f"셔츠{i}", "상의", "셔츠", "white", 2, 1,
                                   image_path=media.save(c.uid, f"p{i}_", buf.getvalue()))
    downloads = []
    real = _Bucket.download
    monkeypatch.setattr(_Bucket, "download", lambda self, path: downloads.append(path) or real(self, path))

    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    at.text_input(key="login_email").input("p@example.com")
    at.text_input(key="login_pw").input("secret123")
    _button(at, "로그인").click().run()
    _goto(at, "내 옷장")
    assert len(downloads) == 3
    cloud.calls.clear()
    downloads.clear()
    at.run()
    assert not at.exception
    assert [t for t, _ in cloud.calls] == ["profiles", "wardrobe_items"] and downloads == []


def test_screens_without_user_say_where_to_go(tmp_path, monkeypatch):
    """사용자가 없을 때 빈 화면으로 멈추지 않고 프로필로 안내한다."""
    at = _local_app(tmp_path, monkeypatch)
    for page in ("내 옷장", "오늘의 추천", "사기 전에 맞춰보기", "저장 코디", "KPI·데이터"):
        _goto(at, page)
        assert any("프로필에서 사용자를 먼저 만들어 주세요" in i.value for i in at.info), page
        assert not at.exception


def test_shopping_screen_judges_candidate_and_buy_adds_it(tmp_path, monkeypatch):
    """사기 전에 맞춰보기: 후보 판정·상황별 점수표가 보이고, '샀어요'를 누르면 옷장에 들어가고 지난 추천이 비워진다."""
    at = _local_app(tmp_path, monkeypatch)
    _make_profile_recommend_and_save(at, "쇼핑")
    uid = db.list_users()[0]["id"]
    db.create_candidate(uid, "베이지 트렌치코트", "아우터", "코트", "beige", 3, 0)
    n_before = len(db.list_wardrobe_items(uid))
    _goto(at, "오늘의 추천")
    _button(at, "TOP 3 추천 생성").click().run()
    assert at.session_state["recommendations"]
    _goto(at, "사기 전에 맞춰보기")
    assert not at.exception
    assert any("베이지 트렌치코트" in m.value for m in at.markdown)
    assert at.dataframe                                              # 상황별 최고 코디 점수 표
    _button(at, "샀어요 · 옷장에 넣기").click().run()
    assert not at.exception
    assert len(db.list_wardrobe_items(uid)) == n_before + 1 and db.list_candidates(uid) == []
    assert at.session_state["recommendations"] is None
