from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import numpy as np
from PIL import Image

from core.colors import hex_to_name
from core.taxonomy import (
    MAIN_CATEGORIES,
    SUBCATEGORY_TO_MAIN,
    canonical_subcategory,
    main_category_for_subcategory,
)

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = ROOT / "models" / "clothing_classifier.pt"
RANKER = ROOT / "models" / "ranker.joblib"
MAIN_CLASSES = MAIN_CATEGORIES

_MODEL_CACHE = None
_LOAD_ERROR = None  # 체크포인트는 있는데 못 불러온 이유 (화면에 그대로 보여 준다)

INSTALL_HINT = (
    "터미널에서 `pip install -r requirements-ai.txt` 를 실행한 뒤 앱을 다시 켜세요. "
    "(GPU가 없으면 `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` 가 더 가볍습니다)"
)


def _torch_installed():
    return find_spec("torch") is not None and find_spec("torchvision") is not None


def model_status():
    if not CHECKPOINT.exists():
        classifier = "수동 확인 모드 (체크포인트 없음)"
    elif not _torch_installed():
        classifier = "체크포인트 있음 · PyTorch 미설치 → requirements-ai.txt 설치 필요"
    else:
        classifier = "학습 체크포인트 연결됨"
    return {
        "classifier": classifier,
        "ranker": "학습 파일 있음 · 추천에는 아직 미연결" if RANKER.exists() else "규칙 기반 (Rule-Based)",
    }

def dominant_color_hex(image: Image.Image):
    img = image.convert("RGB").resize((64, 64))
    arr = np.asarray(img).reshape(-1, 3)
    mask = np.mean(arr, axis=1) < 245
    usable = arr[mask] if mask.any() else arr
    rgb = np.median(usable, axis=0).astype(int)
    return "#{:02x}{:02x}{:02x}".format(*rgb)

def dominant_color_name(image: Image.Image):
    """대표색을 정규 색 이름(black/navy/beige…)으로. 추천의 색상 규칙과 같은 체계를 쓴다."""
    return hex_to_name(dominant_color_hex(image))

def _load_local_classifier():
    global _MODEL_CACHE, _LOAD_ERROR
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if not CHECKPOINT.exists():
        return None

    try:
        import torch
        from torchvision import models, transforms
    except ImportError:
        _LOAD_ERROR = "PyTorch(torch, torchvision)가 설치되어 있지 않아 분류기를 쓸 수 없습니다. " + INSTALL_HINT
        return None

    try:
        checkpoint = torch.load(CHECKPOINT, map_location="cpu")
        classes = checkpoint["classes"]

        model = models.efficientnet_b0(weights=None)
        in_features = model.classifier[1].in_features
        model.classifier[1] = torch.nn.Linear(in_features, len(classes))
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()

        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225],
            ),
        ])
        _MODEL_CACHE = (model, transform, classes, checkpoint.get("task", "auto"))
        _LOAD_ERROR = None
        return _MODEL_CACHE
    except Exception as e:  # 파일 손상, 다른 구조로 학습한 체크포인트 등
        _LOAD_ERROR = f"분류기 파일({CHECKPOINT.name})을 불러오지 못했습니다: {type(e).__name__}: {e}"
        return None

def classify_clothing_image(image: Image.Image):
    loaded = _load_local_classifier()
    if loaded is None:
        if CHECKPOINT.exists():
            return {"ok": False, "message": _LOAD_ERROR or "분류기를 불러오지 못했습니다."}
        return {
            "ok": False,
            "message": (
                f"학습된 체크포인트(models/{CHECKPOINT.name})가 없습니다. 5개 대분류 또는 "
                f"{len(SUBCATEGORY_TO_MAIN)}개 세부분류 모델을 `scripts/train_classifier.py`로 학습하거나, "
                "학습된 파일을 models/ 폴더에 넣으면 자동 분류가 켜집니다."
            ),
        }

    try:
        import torch
        model, transform, classes, task = loaded
        x = transform(image.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            probs = torch.softmax(model(x), dim=1)[0]
            idx = int(torch.argmax(probs))
        label = canonical_subcategory(classes[idx])

        if label in SUBCATEGORY_TO_MAIN:
            subcategory = label
            category = SUBCATEGORY_TO_MAIN[label]
        elif label in MAIN_CLASSES:
            category = label
            subcategory = None
        else:
            category = None
            subcategory = label

        return {
            "ok": True,
            "category": category,
            "subcategory": subcategory,
            "confidence": float(probs[idx]),
            "model": f"EfficientNet-B0 ({task})",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"분류 중 오류: {type(e).__name__}",
        }
