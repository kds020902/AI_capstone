"""가상 착용 Space(tryon_space/app.py)가 옷 사진 주소를 받을 때, 다른 주소로 넘기는 응답(리다이렉트)으로
허용 목록을 빠져나가지 못하는지 확인한다. Space 파일은 GPU 모델을 불러오므로 통째로 import 하지 않고
주소 확인 함수 두 개만 꺼내 가짜 requests 로 실행한다."""
import ast
import io
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "tryon_space" / "app.py"


class FakeGrError(Exception):
    pass


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "PNG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, location=None, body=b""):
        self.status_code, self.headers = status, ({"Location": location} if location else {})
        self.is_redirect = location is not None and status in (301, 302, 303, 307, 308)
        self.raw = SimpleNamespace(read=lambda n, decode_content=True: body[:n])
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def close(self):
        self.closed = True


def _load(routes):
    """_allowed·_fetch 만 꺼내 실행. routes: 주소 → FakeResponse. 요청한 주소와 옵션을 기록한다."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_allowed", "_fetch")]
    assert len(funcs) == 2
    calls = []

    def get(url, **kw):
        calls.append((url, kw))
        return routes[url]

    ns = {"urlparse": urlparse, "urljoin": urljoin, "io": io, "Image": Image,
          "gr": SimpleNamespace(Error=FakeGrError), "requests": SimpleNamespace(get=get),
          "ALLOWED_HOSTS": [".supabase.co"], "ALLOW_HTTP": False,
          "MAX_GARMENT_BYTES": 10 * 1024 * 1024, "MAX_REDIRECTS": 3}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SRC), "exec"), ns)
    return ns["_fetch"], calls


OK = "https://abc.supabase.co/storage/v1/object/sign/closet/a.jpg?token=x"


def test_allowed_url_is_fetched_without_following_redirects_automatically():
    fetch, calls = _load({OK: FakeResponse(body=_png())})
    assert fetch(OK).size == (4, 4)
    assert calls == [(OK, {"timeout": 15, "stream": True, "allow_redirects": False})]


def test_redirect_to_other_host_is_refused():
    fetch, calls = _load({OK: FakeResponse(302, "https://evil.example.com/x.jpg")})
    with pytest.raises(FakeGrError, match="허용하지 않은 주소"):
        fetch(OK)
    assert [u for u, _ in calls] == [OK]                     # 넘어간 주소에는 요청하지 않음


def test_redirect_within_allowed_hosts_is_followed():
    other = "https://abc.supabase.co/storage/v1/object/public/closet/a.jpg"
    fetch, calls = _load({OK: FakeResponse(307, "/storage/v1/object/public/closet/a.jpg"),
                          other: FakeResponse(body=_png())})
    assert fetch(OK).size == (4, 4)
    assert [u for u, _ in calls] == [OK, other]


def test_redirect_loop_stops():
    fetch, _ = _load({OK: FakeResponse(302, OK)})
    with pytest.raises(FakeGrError, match="여러 번"):
        fetch(OK)


def test_plain_http_and_other_hosts_are_refused_before_any_request():
    fetch, calls = _load({})
    for url in ("http://abc.supabase.co/a.jpg", "https://abc.supabase.co.evil.com/a.jpg", "https://evil.com/a.jpg"):
        with pytest.raises(FakeGrError):
            fetch(url)
    assert calls == []
