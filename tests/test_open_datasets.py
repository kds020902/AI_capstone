"""공개 데이터셋 라벨 규칙 테스트 — 눈으로 검수하다 발견한 오분류를 다시 만들지 않도록."""
import pytest

from core.taxonomy import MAIN_CATEGORIES, SUBCATEGORY_TO_MAIN
from scripts.open_datasets import (
    F200K_RULES, GRIGOREV, KREAM_RULES, f200k_label, fpi_label, hnm_label, kream_label, utz_label,
)

VALID = set(SUBCATEGORY_TO_MAIN) | set(MAIN_CATEGORIES)


def test_every_rule_targets_a_known_class():
    labels = {lab for rules in KREAM_RULES.values() for lab, _, _ in rules}
    labels |= {lab for rules in F200K_RULES.values() for lab, _, _ in rules}
    labels |= set(GRIGOREV.values())
    assert labels <= VALID, labels - VALID


@pytest.mark.parametrize("text, expected", [
    ("outer, The North Face 1996 Eco Nuptse Jacket Black, a photography of", "패딩"),
    ("outer, Fetch Polish Goose Challenger Anorak Leopard Charcoal, a photography of", "패딩"),
    ("outer, Polyteru Corduroy Trucker Jacket Olive Gray - 23FW, a photography of", "아우터"),
    ("outer, Palace Denim Jacket Black - 23FW, a photography of", "데님재킷"),
    ("outer, Stone Island Hyper Dense Nylon Field Jacket Dove Grey, a photography of", "야상·필드재킷"),
    ("outer, Nike NSW Windrunner Hooded Jacket Red, a photography of", "바람막이"),
    ("outer, Palace x Carhartt WIP Michigan Coat Dollar Green Camo, a photography of", "아우터"),
    ("top, Nike NSW Long Sleeve T-Shirt Black - US/EU, a photography of", "긴팔 티셔츠"),
    ("top, Uniqlo UT Dragon Ball Graphic T-Shirt Blue - KR, a photography of", "반팔 티셔츠"),
    ("top, Lacoste Shirt Black, a photography of", "셔츠"),
    ("top, Polo Ralph Lauren Cable Knit Sweater Navy, a photography of", "니트"),   # 브랜드명 polo ≠ 폴로
    ("top, Lacoste Paris Long Sleeve Polo Dark Green, a photography of", "폴로"),
    ("top, Lacoste Cable Crewneck Sweater Green, a photography of", "니트"),       # crewneck이어도 니트
    ("top, Millo Jacquard Knit Blouson Dark Gray, a photography of", "상의"),      # 니트 소재 재킷
    ("top, IAB Studio Zip-Up Hoodie Gray - 22FW, a photography of", "후드집업"),
    ("top, Nike Sportswear Club Fleece Pullover Hoodie Black, a photography of", "후드티"),   # 기모 원단 ≠ 플리스
    ("outer, Patagonia Classic Retro-X Fleece Jacket Natural, a photography of", "플리스"),
    ("outer, Nike Tech Fleece Full Zip Hoodie Black, a photography of", "후드집업"),        # 테크 플리스 = 스웨트 원단
    ("outer, The North Face Nuptse Hooded Zip-Up Jacket Black, a photography of", "패딩"),  # 후드+지퍼여도 패딩이 먼저
    ("top, Our Legacy Turtleneck Knit Black, a photography of", "터틀넥"),
    ("bottom, Nike Pro Dri-FIT Leggings Black, a photography of", "레깅스"),
    ("bottom, C.P. Company Light Fleece Mixed Cargo Shorts Blue, a photography of", "반바지"),
    ("bottom, Nanamica Cargo Pants Beige, a photography of", "카고팬츠"),
    ("bottom, Waviness Rain Slub Denim Pants Blue Indigo, a photography of", "청바지"),
])
def test_kream(text, expected):
    assert kream_label(text) == expected


@pytest.mark.parametrize("row, expected", [
    ({"articleType": "Tshirts", "usage": "Casual", "gender": "Men", "productDisplayName": "Proline Men Blue Polo T-shirt"}, "폴로"),
    ({"articleType": "Tshirts", "usage": "Casual", "gender": "Men",
      "productDisplayName": "U.S. Polo Assn. Men Navy T-shirt"}, "반팔 티셔츠"),
    ({"articleType": "Tshirts", "usage": "Sports", "gender": "Men", "productDisplayName": "Nike Men Cricket Blue Jersey"},
     "기능성 티셔츠"),
    ({"articleType": "Tshirts", "usage": "Casual", "gender": "Boys", "productDisplayName": "Doodle Boys Tee"}, None),
    ({"articleType": "Tshirts", "usage": "Casual", "gender": "Unisex",
      "productDisplayName": "Tantra Kid's Unisex Pink Kidswear"}, None),
    ({"articleType": "Trousers", "usage": "Formal", "gender": "Men", "productDisplayName": "Arrow Men Grey Trousers"}, "슬랙스"),
    ({"articleType": "Casual Shoes", "usage": "Casual", "gender": "Men", "productDisplayName": "Vans Men Era Black Shoes"},
     "캔버스화"),
    ({"articleType": "Casual Shoes", "usage": "Casual", "gender": "Men",
      "productDisplayName": "Rockport Men Captoe Brown Casual Shoes"}, "신발"),
    ({"articleType": "Sports Shoes", "usage": "Sports", "gender": "Women",
      "productDisplayName": "Puma Women White Running Shoes"}, "러닝화"),
    ({"articleType": "Watches", "usage": "Casual", "gender": "Men", "productDisplayName": "Titan Men Watch"}, None),
    ({"articleType": "Tshirts", "usage": "Casual", "gender": "Men",
      "productDisplayName": "Locomotive Men Polo Neck Red T-shirt"}, "폴로"),           # Myntra의 polo neck = 폴로 카라
    ({"articleType": "Formal Shoes", "usage": "Formal", "gender": "Men",
      "productDisplayName": "Red Tape Men Black Formal Shoes"}, "구두"),
    ({"articleType": "Flip Flops", "usage": "Casual", "gender": "Men", "productDisplayName": "Puma Men Black Flip Flops"},
     "슬리퍼"),
    ({"articleType": "Heels", "usage": "Casual", "gender": "Women", "productDisplayName": "Catwalk Women Gold Heels"},
     None),                                                                              # 대부분 굽 있는 샌들
])
def test_fpi(row, expected):
    assert fpi_label(row) == expected


@pytest.mark.parametrize("row, expected", [
    ({"category1": "skirts", "category2": "knee length skirts", "category3": "black ponte quilted flare skirt"}, None),
    ({"category1": "jackets", "category2": "padded and down jackets", "category3": "pink quilted down jacket"}, "패딩"),
    ({"category1": "jackets", "category2": "denim jackets", "category3": "blue cropped denim vest"}, None),
    ({"category1": "jackets", "category2": "leather jackets", "category3": "black lamb leather field jacket"}, None),
    ({"category1": "dresses", "category2": "casual and day dresses", "category3": "blue denim shirtdress"}, "셔츠원피스"),
    ({"category1": "dresses", "category2": "prom and formal dresses", "category3": "black ribbed knit dress"}, "니트원피스"),
    ({"category1": "pants", "category2": "cargo pants", "category3": "gray thermal drawstring jogger pants"}, "조거팬츠"),
    ({"category1": "tops", "category2": "blouses", "category3": "multicolor wrap blouse"}, "블라우스"),
])
def test_f200k(row, expected):
    assert f200k_label(row) == expected


@pytest.mark.parametrize("args, expected", [
    (("Shoes", "Sneakers and Athletic Shoes", "Converse", "Canvas"), "캔버스화"),
    (("Shoes", "Sneakers and Athletic Shoes", "ASICS", "Lace;Leather;Mesh;Synthetic"), "러닝화"),
    (("Shoes", "Sneakers and Athletic Shoes", "Skechers", "Mesh;Synthetic"), None),   # 메시인데 러닝 브랜드 아님 → 애매
    (("Shoes", "Sneakers and Athletic Shoes", "Lacoste", "Leather"), "스니커즈"),
    (("Shoes", "Loafers", "Cole Haan", "Leather"), "로퍼"),
    (("Boots", "Ankle", "Bass", "Full-grain leather"), "부츠"),
    (("Shoes", "Oxfords", "ECCO", "Leather"), "구두"),
    (("Shoes", "Heels", "Nine West", "Leather"), "힐"),
    (("Shoes", "Flats", "Steve Madden", "Leather"), "플랫슈즈"),
    (("Sandals", "Flat", "Teva", "Synthetic"), "샌들"),
    (("Slippers", "Slipper Flats", "UGG", "Sheepskin"), None),    # 실내화
])
def test_utzappos(args, expected):
    assert utz_label(*args) == expected


@pytest.mark.parametrize("row, expected", [
    ({"product_type_name": "Hoodie", "prod_name": "ZIP HOOD", "index_group_name": "Divided",
      "detail_desc": "Jacket in sweatshirt fabric with a lined drawstring hood and a zip down the front."}, "후드집업"),
    ({"product_type_name": "Jacket", "prod_name": "Falcon parka", "index_group_name": "Ladieswear",
      "detail_desc": "Padded parka in woven fabric with a lined hood and a zip down the front."}, "패딩"),
    ({"product_type_name": "Jacket", "prod_name": "Milla Pile Jacket", "index_group_name": "Ladieswear",
      "detail_desc": "Jacket in soft pile with a stand-up collar and a zip down the front."}, "플리스"),
    ({"product_type_name": "Jacket", "prod_name": "Lee Parka", "index_group_name": "Menswear",
      "detail_desc": "Parka in cotton twill with a hood lined in soft pile."}, None),          # 안감만 파일
    ({"product_type_name": "Sweater", "prod_name": "Ellen polo-neck", "index_group_name": "Ladieswear",
      "detail_desc": "Fine-knit jumper with a polo neck and long sleeves."}, "터틀넥"),       # 영국식 polo neck
    ({"product_type_name": "Sandals", "prod_name": "Miller slider", "index_group_name": "Menswear",
      "detail_desc": "Sliders with a wide strap."}, "슬리퍼"),
    ({"product_type_name": "Leggings/Tights", "prod_name": "Control Top 50 den 1p Tights", "index_group_name": "Ladieswear",
      "detail_desc": "Tights."}, None),
    ({"product_type_name": "T-shirt", "prod_name": "Kids tee", "index_group_name": "Baby/Children",
      "detail_desc": "T-shirt in cotton jersey with short sleeves."}, None),
])
def test_hnm(row, expected):
    assert hnm_label(row) == expected
