"""체험판 웹 페이지(web/index.html) 만들기.

1) 분류기(models/clothing_classifier.pt)를 브라우저용으로 내보낸다
   - 배치정규화를 conv 가중치에 합치고, 가중치는 float16 → base64 텍스트(web/model/*.b64.txt)
   - 층 구성은 web/model/closet_effnet_b0.json (web/src/closetnet.js 가 읽어 순수 JS로 계산)
2) 규칙표·스타터 옷장·가중치를 파이썬 소스(core/)에서 뽑아 페이지에 넣는다 → 앱과 같은 규칙으로 추천
3) web/src/app_body.html 에 데이터와 JS를 채워 web/index.html 한 파일로 만든다

사용
  python scripts/build_web.py              # 모델 내보내기 + 페이지 조립 (torch 필요)
  python scripts/build_web.py --skip-model # 페이지만 다시 조립
  python -m http.server -d web 8000        # → http://localhost:8000 (모델을 fetch 하므로 파일 더블클릭으로는 안 열림)
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WEB = ROOT / "web"
MODEL_JSON = WEB / "model" / "closet_effnet_b0.json"
MODEL_B64 = WEB / "model" / "closet_effnet_b0.f16.b64.txt"

SKELETON = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


def export_model(checkpoint=ROOT / "models" / "clothing_classifier.pt"):
    import numpy as np
    import torch
    from torchvision import models
    from torchvision.models.efficientnet import MBConv
    from torchvision.ops.misc import Conv2dNormActivation, SqueezeExcitation

    ck = torch.load(checkpoint, map_location="cpu")
    classes = ck["classes"]
    m = models.efficientnet_b0(weights=None)
    m.classifier[1] = torch.nn.Linear(m.classifier[1].in_features, len(classes))
    m.load_state_dict(ck["state_dict"])
    m.eval()

    chunks, offset = [], 0

    def put(arr):
        nonlocal offset
        a = np.ascontiguousarray(arr, dtype=np.float32).ravel()
        chunks.append(a.astype(np.float16))
        start, offset = offset, offset + a.size
        return [start, int(a.size)]

    def conv_op(cna, act):
        conv, bn = cna[0], cna[1]
        scale = (bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        bias = (bn.bias - bn.running_mean * bn.weight / torch.sqrt(bn.running_var + bn.eps)).detach().numpy()
        return {"op": "conv", "cin": conv.in_channels, "cout": conv.out_channels, "k": conv.kernel_size[0],
                "stride": conv.stride[0], "pad": conv.padding[0], "groups": conv.groups, "act": act,
                "w": put(conv.weight.detach().numpy() * scale[:, None, None, None]), "b": put(bias)}

    ops = [conv_op(m.features[0], "silu")]
    for stage in m.features[1:8]:
        for blk in stage:
            assert isinstance(blk, MBConv)
            layers = []
            for layer in blk.block:
                if isinstance(layer, Conv2dNormActivation):
                    layers.append(conv_op(layer, "silu" if len(layer) > 2 else None))
                elif isinstance(layer, SqueezeExcitation):
                    layers.append({"op": "se", "c": layer.fc1.in_channels, "sq": layer.fc1.out_channels,
                                   "w1": put(layer.fc1.weight.detach().numpy()), "b1": put(layer.fc1.bias.detach().numpy()),
                                   "w2": put(layer.fc2.weight.detach().numpy()), "b2": put(layer.fc2.bias.detach().numpy())})
                else:
                    raise ValueError(f"알 수 없는 층: {type(layer)}")
            ops.append({"op": "mbconv", "res": bool(blk.use_res_connect), "layers": layers})
    ops.append(conv_op(m.features[8], "silu"))
    fc = m.classifier[1]
    ops.append({"op": "linear", "cin": fc.in_features, "cout": fc.out_features,
                "w": put(fc.weight.detach().numpy()), "b": put(fc.bias.detach().numpy())})

    blob = np.concatenate(chunks).astype("<f2").tobytes()
    MODEL_JSON.parent.mkdir(parents=True, exist_ok=True)
    MODEL_B64.write_text(base64.b64encode(blob).decode())
    spec = {"classes": classes, "input": [3, 224, 224], "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
            "dtype": "float16", "n_params": offset, "ops": ops,
            "val_accuracy": ck.get("val_accuracy"), "val_macro_accuracy": ck.get("val_macro_accuracy")}
    MODEL_JSON.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    print(f"모델: 파라미터 {offset:,}개 → {MODEL_B64.name} ({MODEL_B64.stat().st_size / 1e6:.1f}MB)")


def closet_data():
    from core import catalog, colors, recommender as R, shopping, taxonomy as T
    return {
        "MAIN_CATEGORIES": T.MAIN_CATEGORIES, "PURPOSES": T.PURPOSES,
        "RULES": {k: {**v, "blocked_purposes": sorted(v["blocked_purposes"])} for k, v in T.RULES.items()},
        "SUBCATEGORIES": T.SUBCATEGORIES, "LEGACY_RENAMES": T.LEGACY_RENAMES,
        "INCOMPATIBLE_PAIRS": sorted(sorted(p) for p in T.INCOMPATIBLE_PAIRS),  # 집합 순서는 실행마다 달라서 정렬
        "LAYER_PAIRS": sorted(list(p) for p in T.LAYER_PAIRS),
        "STARTER": {"male": [list(r) for r in catalog.MALE_STARTER], "female": [list(r) for r in catalog.FEMALE_STARTER]},
        "NEUTRALS": sorted(colors.NEUTRALS), "PALETTE": colors._PALETTE,
        "ALIASES": [[list(keys), name] for keys, name in colors._ALIASES],
        "WEIGHTS": R.WEIGHTS, "RAIN_ROLE_WEIGHT": R.RAIN_ROLE_WEIGHT, "WARMTH_CONTRIB": R.WARMTH_CONTRIB,
        "TARGET_CURVE": R.TARGET_CURVE, "THERMAL_SHIFT": R.THERMAL_SHIFT, "EXERCISE_SHIFT": R.EXERCISE_SHIFT,
        "RAIN_SHIFT": R.RAIN_SHIFT, "OUTER_REQUIRED_AT": R.OUTER_REQUIRED_AT, "LAYER_MAX_TEMP": R.LAYER_MAX_TEMP,
        "WARMTH_TOLERANCE": R.WARMTH_TOLERANCE, "MAX_COMBOS": R.MAX_COMBOS,
        "LATER_DROP": R.LATER_DROP, "LATER_GAP": R.LATER_GAP,
        "SEASONS": T.SEASONS, "SEASON_TEMPS": {k: list(v) for k, v in T.SEASON_TEMPS.items()},
        "CANDIDATE_ID": shopping.CANDIDATE_ID, "IMPROVE_MIN": shopping.IMPROVE_MIN,
    }


def assemble(out=WEB / "index.html", body_only=False):
    src = WEB / "src"
    body = (src / "app_body.html").read_text(encoding="utf-8")
    body = (body.replace("__CLOSET_DATA__", json.dumps(closet_data(), ensure_ascii=False))
                .replace("__CLOSETNET_SRC__", (src / "closetnet.js").read_text(encoding="utf-8"))
                .replace("__CLOSET_JS__", (src / "closet.js").read_text(encoding="utf-8")))
    for placeholder in ("__CLOSET_DATA__", "__CLOSETNET_SRC__", "__CLOSET_JS__"):
        assert placeholder not in body, placeholder
    out.write_text(body if body_only else SKELETON + body + "\n</body>\n</html>\n", encoding="utf-8")
    shown = out.relative_to(ROOT) if out.resolve().is_relative_to(ROOT) else out
    print(f"페이지: {shown} ({out.stat().st_size / 1024:.0f}KB)")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-model", action="store_true", help="모델은 다시 내보내지 않고 페이지만 조립")
    p.add_argument("--body-only", type=Path, help="뼈대(<html><head>) 없이 본문만 이 경로에 저장 (Claude 아티팩트 게시용)")
    args = p.parse_args(argv)
    if not args.skip_model:
        export_model()
    assemble()
    if args.body_only:
        assemble(args.body_only, body_only=True)


if __name__ == "__main__":
    main()
