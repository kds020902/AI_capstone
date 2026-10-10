"""데이터셋 변환 스크립트 테스트 — 각 데이터셋의 폴더/라벨 형식을 작은 가짜 데이터로 흉내 낸다."""
import json

from PIL import Image

from scripts.prepare_dataset import load_label_map, main, map_label


def _img(path, size=(120, 160)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (90, 90, 90)).save(path)


def _classes(out, split="train"):
    d = out / split
    return {c.name: len(list(c.glob("*.jpg"))) for c in d.iterdir()} if d.exists() else {}


def test_label_map_lookup():
    t = load_label_map()
    assert map_label(t, "deepfashion", "Hoodie") == ("후드티", "상의")
    assert map_label(t, "deepfashion", "Jacket") == (None, "아우터")          # 대분류만
    assert map_label(t, "utzappos", "Boots/Ankle") == ("부츠", "신발")        # 상위 폴더로 매핑
    assert map_label(t, "aihub", "청바지") == ("청바지", "하의")               # 클래스명과 같으면 바로
    assert map_label(t, "aihub", "야상/필드재킷") == ("야상·필드재킷", "아우터")  # 옛 이름 변환
    assert map_label(t, "aihub", "모르는라벨") == (None, None)


def test_deepfashion(tmp_path):
    root = tmp_path / "DeepFashion"
    _img(root / "img/Hoodie_A/img_1.jpg")
    _img(root / "img/Jacket_B/img_2.jpg")
    _img(root / "img/Romper_C/img_3.jpg")
    anno = root / "Anno_coarse"
    anno.mkdir(parents=True)
    (anno / "list_category_cloth.txt").write_text("3\ncategory_name category_type\nHoodie 1\nJacket 1\nRomper 3\n")
    (anno / "list_category_img.txt").write_text(
        "3\nimage_name category_label\nimg/Hoodie_A/img_1.jpg 1\nimg/Jacket_B/img_2.jpg 2\nimg/Romper_C/img_3.jpg 3\n")
    (anno / "list_bbox.txt").write_text(
        "3\nimage_name x_1 y_1 x_2 y_2\nimg/Hoodie_A/img_1.jpg 0 0 100 150\n"
        "img/Jacket_B/img_2.jpg 0 0 100 150\nimg/Romper_C/img_3.jpg 0 0 100 150\n")
    eval_dir = root / "Eval"
    eval_dir.mkdir()
    (eval_dir / "list_eval_partition.txt").write_text(
        "3\nimage_name evaluation_status\nimg/Hoodie_A/img_1.jpg train\n"
        "img/Jacket_B/img_2.jpg val\nimg/Romper_C/img_3.jpg test\n")

    out = tmp_path / "sub"
    rep = main(["--source", "deepfashion", "--root", str(root), "--out", str(out)])
    assert _classes(out) == {"후드티": 1}
    assert rep["main_only"] == {"Jacket": 1} and rep["unmapped"] == {"Romper": 1}


def test_deepfashion2_crops_each_item(tmp_path):
    root = tmp_path / "df2"
    _img(root / "train/image/000001.jpg", (300, 300))
    (root / "train/annos").mkdir(parents=True)
    (root / "train/annos/000001.json").write_text(json.dumps({
        "source": "user", "pair_id": 1,
        "item1": {"category_name": "skirt", "category_id": 9, "bounding_box": [10, 10, 200, 250]},
        "item2": {"category_name": "sling", "category_id": 6, "bounding_box": [0, 0, 20, 20]},  # 너무 작음
    }))
    out = tmp_path / "out"
    rep = main(["--source", "deepfashion2", "--root", str(root), "--out", str(out)])
    assert _classes(out) == {"스커트": 1}
    assert rep["skipped"] == {"너무 작음": 1}
    with Image.open(next((out / "train/스커트").glob("*.jpg"))) as im:
        assert im.size == (190, 240)


def test_utzappos(tmp_path):
    root = tmp_path / "ut-zap50k-images-square"
    _img(root / "Shoes/Loafers/BrandA/1.jpg")
    _img(root / "Boots/Ankle/BrandB/2.jpg")
    _img(root / "Slippers/Slipper Flats/BrandC/3.jpg")
    out = tmp_path / "out"
    rep = main(["--source", "utzappos", "--root", str(tmp_path), "--out", str(out), "--val-ratio", "0"])
    assert _classes(out) == {"로퍼": 1, "부츠": 1}
    assert rep["unmapped"] == {"Slippers/Slipper Flats": 1}


def test_csv_for_aihub(tmp_path):
    _img(tmp_path / "imgs/a.jpg")
    _img(tmp_path / "imgs/b.jpg")
    (tmp_path / "labels.csv").write_text(
        "image_path,label,split\nimgs/a.jpg,패딩,train\nimgs/b.jpg,점퍼,val\n", encoding="utf-8")
    out = tmp_path / "out"
    rep = main(["--source", "csv", "--csv", str(tmp_path / "labels.csv"), "--out", str(out)])
    assert _classes(out) == {"패딩": 1}
    assert rep["unmapped"] == {"점퍼": 1}
    assert rep["totals"]["패딩"] == 1 and rep["totals"]["코트"] == 0
