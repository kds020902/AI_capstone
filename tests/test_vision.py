"""분류기를 못 쓸 때 원인(파일 없음 / PyTorch 미설치 / 파일 손상)을 정확히 알려 주는지."""
import sys

import pytest
from PIL import Image

import core.vision as v

IMG = Image.new("RGB", (64, 64), (230, 220, 200))


def _reset(monkeypatch, checkpoint):
    monkeypatch.setattr(v, "CHECKPOINT", checkpoint)
    monkeypatch.setattr(v, "_MODEL_CACHE", None)
    monkeypatch.setattr(v, "_LOAD_ERROR", None)


def test_missing_checkpoint(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path / "없음.pt")
    r = v.classify_clothing_image(IMG)
    assert not r["ok"] and "체크포인트" in r["message"] and "없습니다" in r["message"]
    assert v.model_status()["classifier"].startswith("수동 확인 모드")


def test_checkpoint_present_but_torch_missing(monkeypatch, tmp_path):
    ck = tmp_path / "clothing_classifier.pt"
    ck.write_bytes(b"x")
    _reset(monkeypatch, ck)
    monkeypatch.setitem(sys.modules, "torch", None)       # import torch → ImportError
    monkeypatch.setitem(sys.modules, "torchvision", None)
    monkeypatch.setattr(v, "_torch_installed", lambda: False)
    r = v.classify_clothing_image(IMG)
    assert not r["ok"] and "PyTorch" in r["message"] and "requirements-ai.txt" in r["message"]
    assert "없습니다. 5개" not in r["message"]
    assert "PyTorch 미설치" in v.model_status()["classifier"]


def test_broken_checkpoint_reports_load_error(monkeypatch, tmp_path):
    ck = tmp_path / "clothing_classifier.pt"
    ck.write_bytes(b"not a torch file")
    _reset(monkeypatch, ck)
    r = v.classify_clothing_image(IMG)
    assert not r["ok"] and "불러오지 못했습니다" in r["message"]


def _photo(bg, item, item_box=(16, 16, 48, 48), size=64):
    img = Image.new("RGB", (size, size), bg)
    img.paste(Image.new("RGB", (item_box[2] - item_box[0], item_box[3] - item_box[1]), item), item_box[:2])
    return img


def test_dominant_color_ignores_background():
    """흰 배경에 작은 신발 사진을 '흰색'으로 고르던 문제 — 가장자리 색(배경)을 빼고 고른다."""
    assert v.dominant_color_name(_photo((250, 250, 250), (110, 110, 110), (24, 24, 40, 40))) == "gray"
    assert v.dominant_color_name(_photo((238, 238, 236), (25, 25, 25))) == "black"      # KREAM식 연회색 배경
    assert v.dominant_color_name(_photo((150, 120, 90), (30, 40, 80))) == "navy"        # 방바닥 위 폰 사진


def test_dominant_color_when_item_fills_photo():
    """옷이 사진을 꽉 채우면 가장자리도 옷 색이라 배경을 빼지 않는다."""
    assert v.dominant_color_name(Image.new("RGB", (64, 64), (190, 40, 40))) == "red"


def _fake_model(monkeypatch, classes, logits):
    torch = pytest.importorskip("torch")
    model = lambda x: torch.tensor([logits])  # noqa: E731
    monkeypatch.setattr(v, "_MODEL_CACHE", (model, lambda img: torch.zeros(3, 224, 224), classes, "subcategory"))


def test_top3_candidates_and_low_confidence(monkeypatch):
    """앱도 웹처럼 상위 3개 후보를 보여 준다. 옛 클래스명은 새 이름으로 합쳐 한 번만, 1등 확신이 낮으면 sure=False."""
    _fake_model(monkeypatch, ["청바지", "야상/필드재킷", "야상·필드재킷", "반바지", "슬랙스"], [2.0, 1.5, 1.4, 1.0, 0.5])
    r = v.classify_clothing_image(IMG)
    assert r["ok"] and [c["subcategory"] for c in r["candidates"]] == ["청바지", "야상·필드재킷", "반바지"]
    assert r["subcategory"] == "청바지" and r["category"] == "하의" and r["confidence"] == r["candidates"][0]["confidence"]
    assert r["candidates"][1]["category"] == "아우터"
    conf = [c["confidence"] for c in r["candidates"]]
    assert conf == sorted(conf, reverse=True) and r["confidence"] < v.SURE_CONFIDENCE and r["sure"] is False


def test_sure_when_confident(monkeypatch):
    _fake_model(monkeypatch, ["청바지", "반바지", "슬랙스"], [6.0, 0.0, 0.0])
    r = v.classify_clothing_image(IMG)
    assert r["sure"] is True and r["confidence"] >= v.SURE_CONFIDENCE and len(r["candidates"]) == 3


def test_web_uses_same_confidence_threshold():
    """웹 체험판이 '자동 입력됨 / 후보 중에서 골라 주세요'를 가르는 기준이 앱과 같은지."""
    from scripts.build_web import closet_data
    web = (v.ROOT / "web" / "src" / "app_body.html").read_text(encoding="utf-8")
    assert "conf >= DATA.SURE_CONFIDENCE" in web                      # 숫자를 손으로 옮겨 적지 않고
    assert closet_data()["SURE_CONFIDENCE"] == v.SURE_CONFIDENCE      # 파이썬 값을 그대로 받는다
