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
