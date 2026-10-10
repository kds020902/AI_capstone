"""로그인 없이 받을 수 있는 공개 데이터셋 → prepare_dataset.py 의 csv 형식(image_path,label) 변환.

출처 (라이선스)                                                      라벨을 정하는 방법
  kream     hahminlew/kream-product-blip-captions (CC BY-NC-SA 4.0)   상품명 키워드 (한국 리셀 플랫폼 상품컷)
  fpi       benitomartin/fashion-product-images-small-384x512 (MIT)   articleType + 상품명 (Myntra 상품컷)
  f200k     Marqo/fashion200k (Apache-2.0 표기)                       category2/3 (여성복, 같은 상품의 여러 컷 중 1장만)
  grigorev  alexeygrigorev/clothing-dataset (CC0)                     사람이 붙인 라벨 (폰 사진 — 앱 사용 환경과 가장 비슷)
  utzappos  UT-Zappos50K + meta-data.csv (연구용)                     운동화 폴더를 소재·브랜드로 세분: 캔버스 → 캔버스화,
                                                                       메시+러닝 브랜드 → 러닝화, 가죽·스웨이드 → 스니커즈
  shoe3     keremberke/shoe-classification (Public Domain)            converse 폴더 → 캔버스화
  hnm       H&M 상품 이미지 128px (multabench/core-img-reg-hnm-fashion,   product_type + 상품 설명. 같은 상품의 색상만 다른 컷은 1장만,
            원본 Kaggle H&M 대회 데이터 — 비상업 연구용)               아동복 제외. 해상도가 낮아 이 출처만 있는 클래스가 생기지 않게
                                                                       기존 클래스에도 고르게 섞는다

규칙에 걸리지 않거나 여러 세부분류가 섞인 라벨은 대분류(상의/하의/…)로만 표시한다
→ prepare_dataset.py 에서 학습에서 빠진다 (집계에서 어떤 라벨이 빠졌는지 보려고 남겨 둠).
키워드 규칙은 위에서부터 먼저 맞는 것을 쓴다. 바꾸려면 아래 *_RULES 표를 고친다.

사용 예 (먼저 pip install -r requirements-train.txt)
  python scripts/open_datasets.py download --raw raw
  python scripts/open_datasets.py build --raw raw --out open_csv --max-per-class 500
  python scripts/open_datasets.py build --raw raw --out open_csv --dry-run      # 클래스별 장수와 예시만 출력
  for s in kream fpi f200k grigorev utzappos shoe3 hnm; do
    python scripts/prepare_dataset.py --source csv --csv open_csv/$s.csv --name $s --out dataset_sub
  done
"""
from __future__ import annotations

import argparse
import collections
import csv
import io
import random
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.taxonomy import MAIN_CATEGORIES  # noqa: E402

HF = "https://huggingface.co/datasets"
DOWNLOADS = {
    "kream": [f"{HF}/hahminlew/kream-product-blip-captions/resolve/main/data/{n}.parquet" for n in (
        "train-00000-of-00003-69df8b0a51568204", "train-00001-of-00003-8548ddd0333f4e69",
        "train-00002-of-00003-8b947414615b7489")],
    "fpi": [f"{HF}/benitomartin/fashion-product-images-small-384x512/resolve/main/data/train-0000{i}-of-00005.parquet"
            for i in range(5)],
    "f200k": [f"{HF}/Marqo/fashion200k/resolve/main/data/data-0000{i}-of-00009.parquet" for i in range(9)],
    "utzappos": ["https://vision.cs.utexas.edu/projects/finegrained/utzap50k/ut-zap50k-images-square.zip",
                 "https://vision.cs.utexas.edu/projects/finegrained/utzap50k/ut-zap50k-data.zip"],
    "shoe3": [f"{HF}/keremberke/shoe-classification/resolve/main/data/train.zip"],
    "hnm": [f"{HF}/multabench/core-img-reg-hnm-fashion/resolve/main/data.parquet"]
           + [f"{HF}/multabench/core-img-reg-hnm-fashion/resolve/main/images-{i:05d}.zip" for i in range(42)],
}
GRIGOREV_GIT = "https://github.com/alexeygrigorev/clothing-dataset"


def R(*words):
    """단어 경계 기준 정규식 (영문 소문자 텍스트용)."""
    return re.compile(r"\b(?:" + "|".join(words) + r")\b")


# 후드 + 지퍼 (순서 무관): 'Zip-Up Hoodie', 'Hood Zip-Up', 'Full Zip Hoodie'
ZIP_HOOD = re.compile(r"\b(?:hoodie|hooded|hoody|hood)\b.*\b(?:zip|zipup|full-zip)\b"
                      r"|\b(?:zip|zipup|full-zip)\b.*\b(?:hoodie|hooded|hoody|hood)\b")
TURTLENECK = R("turtleneck", "turtle neck", "turtle-neck", "mock neck", "mock-neck", "mockneck", "roll neck",
               "roll-neck", "funnel neck")
# 'polo neck'은 영국식(H&M)으로는 터틀넥이지만 Myntra에서는 폴로 카라라서 H&M에만 쓴다
TURTLENECK_UK = R("polo neck", "polo-neck", "high collar", "funnel collar")

# (라벨, 포함 정규식, 제외 정규식 or None) — 위에서부터 먼저 맞는 규칙
KREAM_RULES = {
    "outer": [
        ("가디건", R("cardigan"), None),
        ("패딩", R("down", "puffer", "padded", "padding", "nuptse", "puffa", "quilted", "goose"), R("vest", "gilet")),
        ("데님재킷", R("denim jacket", "trucker jacket", "denim trucker"), R("corduroy", "leather", "fleece", "suede")),
        ("야상·필드재킷", R("field jacket", "m-65", "m65", "military jacket", "utility jacket", "fishtail"), None),
        ("봄버재킷", R("bomber", "ma-1", "ma1"), None),
        ("블레이저", R("blazer", "sport coat", "suit jacket"), None),
        ("바람막이", R("windbreaker", "anorak", "windrunner", "wind jacket", "shell jacket", "windshell"), None),
        # 'Club Fleece', 'Tech Fleece'는 기모 스웨트 원단 이름이라 플리스 재킷이 아님
        ("플리스", R("fleece", "sherpa", "boa", "polartec"),
         R("vest", "gilet", "pants", "lined", "lining", "club fleece", "tech fleece")),
        ("후드집업", ZIP_HOOD, R("jacket", "parka", "coat", "vest", "puffer", "padded")),
        ("코트", R("coat", "overcoat", "trench"), R("raincoat", "rain coat", "sport coat", "chore", "michigan")),
    ],
    "top": [
        ("가디건", R("cardigan"), None),
        ("후드집업", ZIP_HOOD, R("vest", "jacket")),
        ("후드티", R("hoodie", "hooded sweatshirt", "hoody"), R("zip", "zip-up", "full-zip")),
        ("터틀넥", TURTLENECK, R("vest", "cardigan", "zip", "jacket", "dress")),
        ("니트", R("knit", "sweater", "cashmere", "mohair"),
         R("sweatshirt", "polo", "vest", "cardigan", "fleece", "blouson", "jacket", "zip", "scarf")),
        ("맨투맨", R("crewneck", "crew neck", "sweatshirt"), R("hood", "hooded")),
        ("폴로", R("polo shirt", "polo"), None),
        ("기능성 티셔츠", R("jersey", "dri-fit", "dri fit", "training top", "running top", "compression"), None),
        ("민소매", R("sleeveless", "tank top", "tank", "singlet"), None),
        # 'shirt'는 't-shirt' 안에서도 맞으므로 셔츠는 티셔츠류를 제외하고 먼저, 남은 긴팔은 티셔츠
        ("셔츠", R("shirt", "oxford", "flannel", "button down", "button-down"), R("t-shirt", "tee", "sweatshirt")),
        ("긴팔 티셔츠", R("long sleeve", "long-sleeve", "longsleeve", "l/s"), None),
        ("반팔 티셔츠", R("t-shirt", "tee", "s/s t"), None),
    ],
    "bottom": [
        ("레깅스", R("leggings", "legging", "tights"), R("shorts", "short")),
        ("반바지", R("shorts", "short pants"), None),
        ("스커트", R("skirt"), None),
        ("카고팬츠", R("cargo"), None),
        ("조거팬츠", R("jogger", "joggers", "sweatpants", "sweat pants", "track pants", "trackpants", "training pants"),
         None),
        ("청바지", R("jeans", "denim pants", "denim"), None),
        ("치노팬츠", R("chino", "chinos"), None),
        ("슬랙스", R("slacks", "trousers", "suit pants", "tailored pants"), None),
    ],
}
KREAM_MAIN = {"outer": "아우터", "top": "상의", "bottom": "하의"}
_POLO_BRANDS = re.compile(r"polo ralph lauren|u\.?s\.? polo assn\.?|polo by ralph lauren")


def kream_label(text):
    cat, _, rest = text.partition(",")
    cat = cat.strip()
    if cat not in KREAM_RULES:
        return None
    name = _POLO_BRANDS.sub("", rest.partition(", a photography")[0].lower())
    return _first(KREAM_RULES[cat], name) or KREAM_MAIN[cat]


def _first(rules, text):
    for label, inc, exc in rules:
        if inc.search(text) and not (exc and exc.search(text)):
            return label
    return None


# ---------------------------------------------------------------- Fashion Product Images (Myntra)
_FPI_DIRECT = {"Shirts": "셔츠", "Jeans": "청바지", "Shorts": "반바지", "Skirts": "스커트", "Track Pants": "조거팬츠",
               "Blazers": "블레이저", "Flip Flops": "슬리퍼", "Sandals": "샌들", "Sports Sandals": "샌들"}
# Leggings는 쿠르타(긴 상의)를 입은 착용컷이라, Heels는 대부분 굽 있는 샌들이라 쓰지 않는다


_KIDS = re.compile(r"\b(kid|kids|kid's|kidswear|boys?|girls?|infant|toddler|baby|layette|youth|junior)\b")


def fpi_label(r):
    a = r.get("articleType") or ""
    name = (r.get("productDisplayName") or "").lower()
    if r.get("gender") in ("Boys", "Girls") or _KIDS.search(name):
        return None
    name_no_brand = _POLO_BRANDS.sub("", name)
    usage = r.get("usage") or ""
    if a in _FPI_DIRECT:
        return _FPI_DIRECT[a]
    if a in ("Tshirts", "Sweaters") and TURTLENECK.search(name):
        return "터틀넥"
    if a == "Tshirts":
        if R("polo").search(name_no_brand):
            return "폴로"
        if R("full sleeve", "long sleeve", "full sleeves").search(name):
            return "긴팔 티셔츠"
        if usage == "Sports":
            return "기능성 티셔츠"
        return "반팔 티셔츠"
    if a == "Sweatshirts":
        return "후드티" if R("hood", "hooded", "hoodie").search(name) else "맨투맨"
    if a == "Sweaters":
        return "가디건" if R("cardigan").search(name) else "니트"
    if a == "Jackets":
        return _first([
            ("데님재킷", R("denim"), None), ("봄버재킷", R("bomber"), None),
            ("바람막이", R("windcheater", "windbreaker", "rain jacket"), None),
            ("패딩", R("padded", "puffer", "quilted", "down"), None),
        ], name) or "아우터"
    if a == "Trousers":
        return _first([("카고팬츠", R("cargo"), None), ("치노팬츠", R("chino", "chinos"), None)], name) \
            or ("슬랙스" if usage == "Formal" else "하의")
    if a == "Dresses":
        return _first([("셔츠원피스", R("shirt dress", "shirtdress"), None),
                       ("니트원피스", R("sweater dress", "knit dress", "jumper dress"), None)], name) or "원피스"
    if a == "Casual Shoes":
        return _first([("캔버스화", R("converse", "vans", "canvas", "keds", "superga"), None),
                       ("로퍼", R("loafer", "loafers", "moccasin", "moccasins"), None),
                       ("부츠", R("boot", "boots"), None)], name) or "신발"  # 캡토·보트슈즈가 섞여 스니커즈로 단정 못 함
    if a == "Sports Shoes":
        return "러닝화" if R("running", "runner", "run").search(name) else "신발"
    if a == "Formal Shoes":
        return "로퍼" if R("loafer", "loafers", "moccasin").search(name) else "구두"
    if a == "Tops":
        return "상의"
    return None


# ---------------------------------------------------------------- Fashion200k (여성복)
# category1(대분류)별로 규칙을 따로 둔다 — 'quilted skirt'가 패딩이 되는 일을 막기 위해
F200K_RULES = {
    "dresses": [
        ("셔츠원피스", R("shirtdress", "shirt dress", "shirt-dress"), None),
        ("니트원피스", R("sweater dress", "knit dress", "knitted dress", "sweater-dress", "jumper dress",
                     "ribbed dress"), None),
    ],
    "jackets": [
        ("패딩", R("padded and down jackets"), R("vest", "gilet", "leather", "fur")),
        ("데님재킷", R("denim jackets", "jean jacket", "trucker jacket"), R("vest", "blazer", "skirt", "dress")),
        ("봄버재킷", R("bomber"), R("vest", "gilet")),
        ("야상·필드재킷", R("field jacket", "military jacket", "utility jacket", "army jacket", "safari jacket"),
         R("leather jackets", "waistcoats and gilets", "vest", "transparent")),
        ("블레이저", R("blazers and suit jackets"), R("cardigan", "bolero", "vest", "kimono")),
        ("플리스", R("fleece", "sherpa", "teddy"), R("vest", "gilet", "lined", "leather", "waistcoats")),
    ],
    "pants": [
        ("레깅스", R("leggings"), R("shorts", "skirt")),
        ("카고팬츠", R("cargo"), R("jogger", "shorts", "skirt")),
        ("조거팬츠", R("jogger", "joggers", "track pants", "sweatpants"), None),
    ],
    "tops": [
        ("터틀넥", TURTLENECK, R("sleeveless", "vest", "cardigan", "dress", "blouses")),
        ("블라우스", R("blouses"), None),
        ("민소매", R("sleeveless and tank tops"), R("blouse", "shirt")),
    ],
}


def f200k_label(r):
    rules = F200K_RULES.get((r.get("category1") or "").lower())
    if not rules:
        return None
    text = f"{r.get('category2') or ''} | {r.get('category3') or ''}".lower()
    return _first(rules, text)


# ---------------------------------------------------------------- clothing-dataset (Grigorev)
GRIGOREV = {
    "T-Shirt": "반팔 티셔츠", "Polo": "폴로", "Hoodie": "후드티", "Blazer": "블레이저", "Shirt": "셔츠",
    "Blouse": "블라우스", "Dress": "원피스", "Skirt": "스커트", "Shorts": "반바지",
    # 여러 세부분류가 섞임 → 대분류만
    "Longsleeve": "상의", "Undershirt": "상의", "Top": "상의", "Pants": "하의", "Outwear": "아우터", "Shoes": "신발",
}


# ---------------------------------------------------------------- UT-Zappos50K (메타데이터로 운동화 세분)
RUNNING_BRANDS = {"ASICS", "Saucony", "Brooks", "Mizuno", "adidas Running", "Salomon", "Hoka One One", "Newton Running",
                  "Altra Zero Drop Footwear", "Pearl Izumi", "Under Armour", "Nike"}
CANVAS_BRANDS = {"Converse", "Vans", "Keds", "Superga", "TOMS"}


_UTZ_SHOES = {"Loafers": "로퍼", "Oxfords": "구두", "Heels": "힐", "Flats": "플랫슈즈"}


def utz_label(category, subcategory, brand, material):
    m = (material or "").lower()
    if category == "Boots":
        return "부츠"
    if category == "Sandals":
        return "샌들"
    if category != "Shoes":
        return None  # Slippers는 실내화라 뺌
    if subcategory in _UTZ_SHOES:
        return _UTZ_SHOES[subcategory]
    if subcategory != "Sneakers and Athletic Shoes":
        return None
    if "canvas" in m or brand in CANVAS_BRANDS:
        return "캔버스화"
    if "mesh" in m:
        return "러닝화" if brand in RUNNING_BRANDS else None  # 메시인데 러닝 브랜드가 아니면 애매 → 버림
    if any(k in m for k in ("leather", "suede", "nubuck")):
        return "스니커즈"
    return None


# ---------------------------------------------------------------- H&M (상품 설명으로 세분)
_HNM_TYPE = {"Blouse": "블라우스", "Polo shirt": "폴로", "Vest top": "민소매", "Shorts": "반바지", "Skirt": "스커트",
             "Blazer": "블레이저", "Cardigan": "가디건", "Boots": "부츠", "Flip flop": "슬리퍼", "Ballerinas": "플랫슈즈",
             "Pumps": "힐", "Heels": "힐", "Heeled sandals": "샌들"}
_HNM_FLEECE = re.compile(r"\bin (?:\w+[ -]){0,3}(?:fleece|pile|teddy|borg|faux shearling)\b")
_SLIDES = R("slider", "sliders", "slide", "slides", "flip-flop", "flip-flops", "flipflop", "flip flop", "pool")


def hnm_label(r):
    if r.get("index_group_name") == "Baby/Children":
        return None
    t = r.get("product_type_name") or ""
    name = (r.get("prod_name") or "").lower()
    desc = (r.get("detail_desc") or "").lower()
    text = f"{name} {desc}"
    if t == "Sandals":
        return "슬리퍼" if _SLIDES.search(name) else "샌들"
    if t == "Leggings/Tights":
        return "레깅스" if R("leggings").search(name) and not R("tights", "pack", "1p", "cycling", "shorts", "den").search(name) \
            else None
    if t in ("Hoodie", "Jacket", "Cardigan") and "sweatshirt fabric" in desc and ZIP_HOOD.search(text) \
            and not R("padded", "lined", "parka", "puffer", "down").search(name):
        return "후드집업"
    if t in ("Jacket", "Hoodie", "Sweater", "Cardigan") and _HNM_FLEECE.search(re.split(r" with |\. ", desc)[0]):
        return "플리스"  # 첫 구절('Jacket in soft pile')로만 판단 — 'lined with pile'인 파카 제외
    if t in ("Sweater", "Top", "T-shirt") and (TURTLENECK.search(text) or TURTLENECK_UK.search(text)) \
            and not R("zip", "sleeveless").search(text):
        return "터틀넥"
    if t in _HNM_TYPE:
        return _HNM_TYPE[t]
    if t == "Hoodie":
        return None if R("zip", "padded").search(text) else "후드티"
    if t == "T-shirt":
        if R("functional fabric", "fast-drying").search(desc):
            return "기능성 티셔츠"
        return "긴팔 티셔츠" if R("long sleeves").search(desc) else "반팔 티셔츠" if R("short sleeves").search(desc) else None
    if t == "Shirt":
        return None if R("jacket", "overshirt").search(text) else "셔츠"
    if t == "Sweater":
        if R("knit", "knitted", "fine-knit", "rib-knit", "wool", "cashmere").search(desc):
            return "니트"
        return "맨투맨" if "sweatshirt" in text and not R("hood", "zip").search(text) else None
    if t == "Trousers":
        return _first([("청바지", R("jeans", "denim"), None), ("조거팬츠", R("joggers", "sweatpants", "sweatshirt fabric"), None),
                       ("카고팬츠", R("cargo"), None), ("치노팬츠", R("chinos", "chino"), None),
                       ("슬랙스", R("tailored", "suit trousers", "pressed creases"), None)], text)
    if t == "Dress":
        return _first([("셔츠원피스", R("shirt dress", "shirtdress"), None),
                       ("니트원피스", R("knitted dress", "fine-knit", "rib-knit", "knit dress"), None)], text) or "원피스"
    if t == "Jacket":
        return _first([("데님재킷", R("denim"), R("padded", "lined")), ("봄버재킷", R("bomber"), None),
                       ("패딩", R("padded", "puffer", "down jacket", "down-filled", "down filling"), None),  # 'zip down the front'의 down 제외
                       ("바람막이", R("windbreaker", "anorak"), None)], text)
    if t == "Coat":
        return None if R("padded", "puffer", "down jacket", "down-filled", "parka", "pile", "teddy").search(text) else "코트"
    return None


def _hnm_candidates(raw):
    """(이미지 경로, 라벨, 설명). 같은 상품(사진 파일명의 앞 7자리)의 색상 변형은 첫 장만 쓰고, 필요한 사진만 압축에서 꺼낸다."""
    import pyarrow.parquet as pq
    root = raw / "hnm"
    rows, seen = [], set()
    for r in pq.read_table(root / "data.parquet").to_pylist():
        code = r["ProductPic"].rsplit("_", 1)[-1][:7]
        if code in seen:
            continue
        seen.add(code)
        label = hnm_label(r)
        if label:
            rows.append((r["ProductPic"], label, f"{r['product_type_name']} | {r['prod_name']} | {(r['detail_desc'] or '')[:80]}"))
    out_dir = root / "extracted"
    wanted = {pic for pic, _, _ in rows if not (out_dir / pic).exists()}
    for z in sorted(root.glob("images-*.zip")) if wanted else []:
        with zipfile.ZipFile(z) as zf:
            for name in wanted.intersection(zf.namelist()):
                (out_dir / name).parent.mkdir(parents=True, exist_ok=True)
                (out_dir / name).write_bytes(zf.read(name))
    return [(out_dir / pic, label, desc) for pic, label, desc in rows if (out_dir / pic).exists()]


# ---------------------------------------------------------------- 공통
def _save_image(data, path, max_side=512):
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, quality=90)


def _parquet_rows(files, label_fn, columns):
    """(파일, 행 그룹, 행 번호, 라벨, 원본 설명) — 이미지 열은 읽지 않는다."""
    import pyarrow.parquet as pq
    for f in files:
        pf = pq.ParquetFile(f)
        for g in range(pf.num_row_groups):
            for i, r in enumerate(pf.read_row_group(g, columns=columns).to_pylist()):
                label = label_fn(r)
                if label:
                    yield f, g, i, label, " | ".join(str(r.get(c) or "") for c in columns)


def _extract_parquet(picked, out_dir, source):
    """고른 행의 이미지만 꺼내 저장. picked: [(파일, 그룹, 행, 라벨, 설명)] → [(경로, 라벨, 설명)]"""
    import pyarrow.parquet as pq
    by_group = collections.defaultdict(list)
    for item in picked:
        by_group[(item[0], item[1])].append(item)
    rows = []
    for (f, g), items in sorted(by_group.items()):
        images = pq.ParquetFile(f).read_row_group(g, columns=["image"]).column("image").to_pylist()
        for _, _, i, label, desc in items:
            path = out_dir / "images" / source / f"{Path(f).stem[:24]}_{g:04d}_{i:05d}.jpg"
            if not path.exists():
                _save_image(images[i]["bytes"], path)
            rows.append((path, label, desc))
    return rows


def _cap(candidates, max_per_class, seed):
    rng = random.Random(seed)
    rng.shuffle(candidates)
    kept, n = [], collections.Counter()
    for c in candidates:
        label = c[3] if len(c) == 5 else c[1]
        if max_per_class and n[label] >= max_per_class:
            continue
        n[label] += 1
        kept.append(c)
    return kept


def collect(source, raw):
    """[(… , 라벨, 설명)] 후보 목록. parquet 출처는 5튜플, 파일 출처는 (경로, 라벨, 설명)."""
    if source == "kream":
        return list(_parquet_rows(sorted((raw / "kream").glob("*.parquet")), lambda r: kream_label(r["text"]), ["text"]))
    if source == "fpi":
        cols = ["articleType", "usage", "gender", "productDisplayName"]
        return list(_parquet_rows(sorted((raw / "fpi").glob("*.parquet")), fpi_label, cols))
    if source == "f200k":
        seen, out = set(), []
        for item in _parquet_rows(sorted((raw / "f200k").glob("*.parquet")), f200k_label,
                                  ["category1", "category2", "category3", "item_ID"]):
            base = item[4].split(" | ")[-1].rsplit("_", 1)[0]  # 같은 상품의 다른 컷은 하나만 (train/val 누수 방지)
            if base not in seen:
                seen.add(base)
                out.append(item)
        return out
    if source == "grigorev":
        root = raw / "grigorev"
        out = []
        with open(root / "images.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                label = GRIGOREV.get(r["label"])
                if label and r.get("kids", "").lower() != "true":
                    out.append((root / "images" / f"{r['image']}.jpg", label, r["label"]))
        return out
    if source == "utzappos":
        meta = next(raw.rglob("meta-data.csv"))
        with open(meta, encoding="utf-8", errors="replace") as f:
            info = {r["CID"]: r for r in csv.DictReader(f)}
        img_root = next(p for p in raw.rglob("ut-zap50k-images-square") if p.is_dir())
        out = []
        for path in sorted(img_root.rglob("*.jpg")):
            parts = path.relative_to(img_root).parts  # Category/SubCategory/Brand/file
            if len(parts) < 4 or _KIDS.search(parts[2].lower()):
                continue
            a, _, b = path.stem.partition(".")
            r = info.get(f"{a}-{b}", {})
            label = utz_label(parts[0], parts[1], parts[2], r.get("Material"))
            if label:
                out.append((path, label, f"{parts[1]} | {parts[2]} | {r.get('Material', '')}"))
        return out
    if source == "hnm":
        return _hnm_candidates(raw)
    if source == "shoe3":
        root = raw / "shoe3"
        out = []
        for path in sorted(root.rglob("*.jpg")):
            brand = path.parent.name.lower()
            label = {"converse": "캔버스화", "adidas": "신발", "nike": "신발"}.get(brand)
            if label:
                out.append((path, label, brand))
        return out
    raise ValueError(source)


SOURCES = ["kream", "fpi", "f200k", "grigorev", "utzappos", "shoe3", "hnm"]


def build(raw, out, sources, max_per_class, seed, dry_run, examples):
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for source in sources:
        cands = collect(source, raw)
        kept = _cap(cands, max_per_class, seed)
        counts = collections.Counter(c[3] if len(c) == 5 else c[1] for c in kept)
        report[source] = counts
        print(f"\n[{source}] 후보 {len(cands)} → 사용 {len(kept)}")
        for label, n in sorted(counts.items(), key=lambda x: (x[0] in MAIN_CATEGORIES, -x[1])):
            ex = [c[4] if len(c) == 5 else c[2] for c in kept if (c[3] if len(c) == 5 else c[1]) == label][:examples]
            print(f"  {label}: {n}" + ("".join(f"\n      · {e[:110]}" for e in ex) if ex else ""))
        if dry_run:
            continue
        rows = _extract_parquet(kept, out, source) if kept and len(kept[0]) == 5 else kept
        with open(out / f"{source}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["image_path", "label", "source_label"])
            for path, label, desc in rows:
                w.writerow([Path(path).resolve(), label, desc])
    return report


def download(raw, sources):
    raw.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source == "grigorev":
            if not (raw / "grigorev").exists():
                subprocess.run(["git", "clone", "--depth", "1", GRIGOREV_GIT, str(raw / "grigorev")], check=True)
            continue
        for url in DOWNLOADS[source]:
            dst = raw / source / url.rsplit("/", 1)[-1]
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            print("download", url)
            urllib.request.urlretrieve(url, dst)
            if dst.suffix == ".zip" and source != "hnm":  # hnm은 필요한 사진만 build 때 꺼냄 (20만 장)
                with zipfile.ZipFile(dst) as z:
                    z.extractall(dst.parent)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["download", "build"])
    p.add_argument("--raw", type=Path, required=True, help="원본을 받아 둔 폴더")
    p.add_argument("--out", type=Path, help="build 결과 (출처별 csv + 꺼낸 이미지)")
    p.add_argument("--sources", default=",".join(SOURCES))
    p.add_argument("--max-per-class", type=int, default=500, help="출처 하나에서 클래스당 최대 장수 (0=제한 없음)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--examples", type=int, default=0, help="클래스마다 원본 설명 예시를 몇 개 출력할지")
    args = p.parse_args(argv)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    if args.command == "download":
        return download(args.raw, sources)
    if not args.out and not args.dry_run:
        p.error("build 에는 --out 이 필요합니다.")
    return build(args.raw, args.out or Path("."), sources, args.max_per_class, args.seed, args.dry_run, args.examples)


if __name__ == "__main__":
    main()
