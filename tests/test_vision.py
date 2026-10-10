"""분류기를 못 쓸 때 원인(파일 없음 / PyTorch 미설치 / 파일 손상)을 정확히 알려 주는지."""
import sys

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
