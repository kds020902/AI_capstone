"""공개 배포 보조 기능: 설정 읽기·비밀 키 거부, 사진 저장소, 가상 착용 연결 주소."""
import base64
import io
import json
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from core import config
from core.media import SB_PREFIX, LocalMedia, SupabaseMedia
from core.tryon import space_addresses, tryon_default, tryon_kind, tryon_link, tryon_order, tryon_supported
from tests.fake_supabase import FakeBackend, signed_in_client


def _jwt(payload):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'HS256'})}.{enc(payload)}.sig"


def _png(w=2000, h=1500, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------ 설정
def test_local_mode_when_supabase_settings_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    assert config.cloud_settings() is None


def test_cloud_settings_from_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co/")
    monkeypatch.setenv("SUPABASE_KEY", _jwt({"role": "anon"}))
    s = config.cloud_settings()
    assert s["url"] == "https://abc.supabase.co"


@pytest.mark.parametrize("pasted", [
    "https://supabase.com/dashboard/project/qskvxjpfqnurmjwwtaor",          # 대시보드 주소를 그대로 붙여 넣은 경우
    "https://supabase.com/dashboard/project/qskvxjpfqnurmjwwtaor/settings/api",
    " https://qskvxjpfqnurmjwwtaor.supabase.co/rest/v1/ ",
    "https://qskvxjpfqnurmjwwtaor.supabase.co",
])
def test_supabase_url_paste_mistakes_are_fixed(pasted):
    assert config.normalize_supabase_url(pasted) == "https://qskvxjpfqnurmjwwtaor.supabase.co"


def test_html_reply_explains_wrong_url():
    from core import cloud_auth
    err = ValueError("1 validation error for User Invalid JSON: expected value at line 1 column 1 "
                     "[type=json_invalid, input_value=b'<!DOCTYPE html><html la...', input_type=bytes]")
    assert "SUPABASE_URL" in cloud_auth._msg(err)


@pytest.mark.parametrize("key", [_jwt({"role": "service_role"}), "sb_secret_abcdef"])
def test_server_only_keys_are_refused(monkeypatch, key):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", key)
    with pytest.raises(config.UnsafeKeyError):
        config.cloud_settings()


def test_publishable_key_is_accepted():
    assert config.check_public_key("sb_publishable_xyz") == "sb_publishable_xyz"


# ------------------------------------------------------------ 사진 저장소
def test_local_media_round_trip(tmp_path):
    m = LocalMedia(tmp_path)
    ref = m.save(7, "cand_", b"abc", ".png")
    assert ref.endswith(".png") and "cand_7_" in ref
    assert m.read(ref) == b"abc" and m.read(str(tmp_path / "none.jpg")) is None
    assert m.read(SB_PREFIX + "x/y.jpg") is None and m.signed_url(ref) is None


def test_supabase_media_saves_small_jpeg_in_own_folder():
    backend = FakeBackend()
    a = signed_in_client(backend, "a@example.com")
    m = SupabaseMedia(a, a.uid)
    ref = m.save(a.uid, "", _png(), ".png")
    assert ref.startswith(SB_PREFIX + a.uid + "/") and ref.endswith(".jpg")
    img = Image.open(io.BytesIO(SupabaseMedia(a, a.uid).read(ref)))   # 새 인스턴스: 저장소에서 내려받기
    assert img.format == "JPEG" and max(img.size) == 1280
    assert "token=" in m.signed_url(ref)
    with pytest.raises(PermissionError):
        m.save("someone-else", "", _png(), ".png")


def test_supabase_media_cannot_read_other_users_photo():
    backend = FakeBackend()
    a = signed_in_client(backend, "a@example.com")
    b = signed_in_client(backend, "b@example.com")
    ref = SupabaseMedia(a, a.uid).save(a.uid, "", _png(10, 10), ".png")
    mb = SupabaseMedia(b, b.uid)
    assert mb.read(ref) is None and mb.signed_url(ref) is None
    assert mb.read("/local/path.jpg") is None


# ------------------------------------------------------------ 가상 착용 연결
def test_tryon_kind_and_order():
    assert [tryon_kind(c) for c in ("상의", "하의", "원피스", "아우터", "신발")] == ["upper", "lower", "overall", "outer", None]
    items = [{"name": "청바지", "category": "하의"}, {"name": "운동화", "category": "신발"},
             {"name": "셔츠", "category": "상의"}, {"name": "코트", "category": "아우터"}]
    assert [x["name"] for x in tryon_order(items)] == ["셔츠", "코트"]          # 최대 2벌, 하의는 뒤로
    assert [x["name"] for x in tryon_order(items[:3])] == ["셔츠", "청바지"]
    assert [x["name"] for x in tryon_supported(items)] == ["청바지", "셔츠", "코트"]
    # 상의+하의+아우터 코디는 기본으로 상의·하의를 고르고, 입히는 순서는 하의가 마지막
    assert [x["name"] for x in tryon_default(items)] == ["셔츠", "청바지"]
    assert [x["name"] for x in tryon_default([items[3], items[0]])] == ["코트", "청바지"]


def test_space_addresses_from_page_or_direct_url():
    assert space_addresses("https://huggingface.co/spaces/Me_Kim/closet.TryOn") == (
        "https://me-kim-closet-tryon.hf.space/", "https://huggingface.co/spaces/Me_Kim/closet.TryOn")
    assert space_addresses(" https://huggingface.co/spaces/me/tryon/ ") == (
        "https://me-tryon.hf.space/", "https://huggingface.co/spaces/me/tryon")
    assert space_addresses("https://me-tryon.hf.space") == ("https://me-tryon.hf.space/", "https://me-tryon.hf.space/")


def test_tryon_link_encodes_signed_urls():
    embed, page = space_addresses("https://huggingface.co/spaces/me/closet-tryon")
    garments = [("https://x.supabase.co/a?token=1&b=2", "upper"), (None, "lower")]
    url = tryon_link(embed, garments)
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://me-closet-tryon.hf.space/?")
    assert q == {"garment": ["https://x.supabase.co/a?token=1&b=2"], "kind": ["upper"], "kind2": ["lower"]}
    assert tryon_link(page, garments).startswith("https://huggingface.co/spaces/me/closet-tryon?garment=")
    assert tryon_link("https://s.hf.space/") == "https://s.hf.space/"
