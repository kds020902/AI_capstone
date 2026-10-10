"""웹 체험판(자바스크립트)이 앱(파이썬)과 같은 결과를 내는지. node가 없으면 건너뛴다.

- 추천: 같은 옷장·조건에서 '모든 후보 코디의 점수'와 경고 문구, 저녁 대비 겉옷 안내가 같아야 한다
- 분류기: web/model 의 가중치(float16)로 계산한 로짓이 PyTorch 체크포인트와 거의 같아야 한다
"""
import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import recommender as R
from core.shopping import candidate_report
from core.taxonomy import RULES, current_season, item_seasons, season_label
from scripts.build_web import closet_data
from tests.helpers import THERMAL, starter_items

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node가 없어 웹 체험판 비교를 건너뜀")


def _entries(results):
    def advice(r):
        return next((t for t in r["reasons"] if "쯤 체감" in t), "")
    return sorted(",".join(str(i) for i in sorted(p["id"] for p in r["pieces"])) + "=" + f"{r['score']:.4f}"
                  + "|" + (str(r["carry"]["id"]) if r.get("carry") else "") + "|" + advice(r) for r in results)


def test_js_recommender_matches_python(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_pick_diverse", lambda scored, k: scored)  # 다양화 전 전체 후보로 비교
    cases, expected = [], []
    for gender, purpose, t, rain, thermal, drop in itertools.product(
            ["male", "female"], ["등교", "데이트", "운동", "격식"], [-5, 5, 15, 25, 33], [False, True],
            ["보통", "추위를 잘 탐", "더위를 잘 탐"], [None, 12]):
        cold, heat = THERMAL[thermal]
        items = starter_items(gender)
        later = None if drop is None else {"temperature": t - drop, "apparent_temperature": t - drop, "label": "21시"}
        out = R.recommend(items, t, t, 60, 0, rain, 8, purpose, cold, heat, 3, later=later)
        expected.append({"entries": _entries(out["results"]), "eff": out["context"]["eff"], "warnings": out["warnings"]})
        args = {"temperature": t, "apparent": t, "rain": rain, "purpose": purpose, "cold": cold, "heat": heat}
        if later:
            args["later"] = {"temperature": t - drop, "apparent": t - drop, "label": "21시"}
        cases.append({"items": items, "args": args})
    src = tmp_path / "cases.json"
    src.write_text(json.dumps({"data": closet_data(), "cases": cases}, ensure_ascii=False), encoding="utf-8")
    got = json.loads(subprocess.run([NODE, str(ROOT / "tests/js/recommend.js"), str(src)],
                                    capture_output=True, text=True, check=True).stdout)
    for case, exp, js in zip(cases, expected, got):
        assert js["entries"] == exp["entries"], case["args"]
        assert abs(js["eff"] - exp["eff"]) < 1e-9 and js["warnings"] == exp["warnings"], case["args"]


def test_js_seasons_match_python(tmp_path):
    items = [{"subcategory": sub, "seasons": None} for sub in RULES]
    items += [{"subcategory": "반팔 티셔츠", "seasons": "봄·가을|여름"}, {"subcategory": "패딩", "seasons": "겨울|모름"},
              {"subcategory": "없는 종류", "seasons": ""}]
    src = tmp_path / "items.json"
    src.write_text(json.dumps({"data": closet_data(), "items": items}, ensure_ascii=False), encoding="utf-8")
    got = json.loads(subprocess.run([NODE, str(ROOT / "tests/js/seasons.js"), str(src)],
                                    capture_output=True, text=True, check=True).stdout)
    for item, js in zip(items, got["items"]):
        assert js["seasons"] == item_seasons(item) and js["label"] == season_label(item_seasons(item)), item
    assert got["months"] == [current_season(m) for m in range(1, 13)]


def test_js_shopping_report_matches_python(tmp_path):
    def cand(name, cat, sub, color, warmth):
        return {"name": name, "category": cat, "subcategory": sub, "color": color, "warmth": warmth, "rain_ok": 0}
    keep = ("반팔 티셔츠", "반바지", "청바지", "슬랙스", "셔츠", "스니커즈", "로퍼", "블레이저", "가디건")
    summer = [x for x in starter_items("male") if x["subcategory"] in keep]
    cases = [(cand("베이지 트렌치코트", "아우터", "코트", "beige", 3), starter_items("male")),     # fit
             (cand("흰 반팔", "상의", "반팔 티셔츠", "white", 1), starter_items("male")),          # dup
             (cand("그레이 니트", "상의", "니트", "gray", 3), summer),                             # gap
             (cand("실버 힐", "신발", "힐", "gray", 1), starter_items("female")),
             (cand("레드 니트", "상의", "니트", "red", 3), [x for x in starter_items("female") if x["category"] != "신발"])]
    src = tmp_path / "shop.json"
    src.write_text(json.dumps({"data": closet_data(), "cases": [{"cand": c, "items": i} for c, i in cases]},
                              ensure_ascii=False), encoding="utf-8")
    got = json.loads(subprocess.run([NODE, str(ROOT / "tests/js/shopping.js"), str(src)],
                                    capture_output=True, text=True, check=True).stdout)
    for (c, items), js in zip(cases, got):
        r = candidate_report(c, items)
        assert js["verdict"] == list(r["verdict"]) and js["total"] == r["total"], c["name"]
        assert js["partners"] == r["partners"] and js["seasons"] == r["seasons"], c["name"]
        assert js["improves"] == [[x["season"], x["purpose"], x["before"], x["after"]] for x in r["improves"]], c["name"]
        assert js["same_type"] == [x["name"] for x in r["similar"]["same_type"]], c["name"]
        assert js["same_color"] == [x["name"] for x in r["similar"]["same_color"]], c["name"]
        for (season, purpose), cell in r["cells"].items():
            n, best, without, temp, scores = js["cells"][f"{season}|{purpose}"]
            assert (n, best, without, temp) == (cell["n"], cell["best"], cell["best_without"], cell["temp"]), (c["name"], season, purpose)
            assert scores == [x["score"] for x in cell["results"]], (c["name"], season, purpose)


def test_js_classifier_matches_pytorch(tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    from PIL import Image, ImageDraw
    from torchvision import models, transforms

    ck = torch.load(ROOT / "models/clothing_classifier.pt", map_location="cpu")
    m = models.efficientnet_b0(weights=None)
    m.classifier[1] = torch.nn.Linear(m.classifier[1].in_features, len(ck["classes"]))
    m.load_state_dict(ck["state_dict"])
    m.eval()
    tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    images = []
    for bg, fg, box in [((245, 245, 245), (30, 40, 80), (60, 30, 160, 200)), ((150, 120, 90), (200, 60, 60), (40, 70, 190, 160))]:
        img = Image.new("RGB", (224, 224), bg)
        ImageDraw.Draw(img).rectangle(box, fill=fg)
        images.append(tf(img))
    x = torch.stack(images)
    with torch.no_grad():
        ref = m(x)
    src = tmp_path / "x.f32"
    x.numpy().astype("<f4").tofile(src)
    got = json.loads(subprocess.run([NODE, str(ROOT / "tests/js/classify.js"), str(src), str(len(images))],
                                    capture_output=True, text=True, check=True).stdout)
    assert got["classes"] == list(ck["classes"]), "web/model 이 models/clothing_classifier.pt 와 다른 모델입니다 (build_web.py 다시 실행)"
    js = torch.tensor(got["logits"])
    assert (js - ref).abs().max() < 0.25  # float16 가중치라 약간의 차이는 있음
    assert torch.equal(js.argmax(1), ref.argmax(1))
