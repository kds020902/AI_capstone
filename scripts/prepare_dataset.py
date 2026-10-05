"""여러 공개 데이터셋을 이 프로젝트의 분류 클래스로 모아 ImageFolder 구조를 만든다.

    dataset_sub/train/<세부분류>/*.jpg     (--task sub,  33종)
    dataset_main/train/<대분류>/*.jpg      (--task main, 5종)

라벨 변환표: data/dataset_label_map.csv  (source, source_label → subcategory, main_category)
- 세부분류가 비어 있는 행은 '대분류 학습에만' 쓰인다 (--task main 일 때만 저장).
- 표에 없는 라벨은 건너뛰고, 끝에 '매핑 안 된 라벨' 목록을 출력한다 → 표에 행을 추가하면 된다.
- 라벨이 이 프로젝트 클래스명과 똑같으면(예: AI-Hub의 '청바지') 표 없이 바로 매핑된다.

여러 번 실행하면 같은 출력 폴더에 누적된다 (파일명 앞에 출처가 붙어 겹치지 않음).

사용 예
  python scripts/prepare_dataset.py --source deepfashion  --root /data/DeepFashion            --out dataset_sub
  python scripts/prepare_dataset.py --source deepfashion2 --root /data/deepfashion2           --out dataset_sub
  python scripts/prepare_dataset.py --source utzappos     --root /data/ut-zap50k-images-square --out dataset_sub
  python scripts/prepare_dataset.py --source csv --csv aihub_labels.csv --name aihub         --out dataset_sub

csv 형식 (AI-Hub 등 JSON 라벨을 직접 변환해서 쓸 때)
  image_path,label[,x1,y1,x2,y2][,split]   — image_path는 csv 파일 기준 상대경로 가능, split은 train/val
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.taxonomy import MAIN_CATEGORIES, SUBCATEGORY_TO_MAIN, canonical_subcategory  # noqa: E402

LABEL_MAP = ROOT / "data" / "dataset_label_map.csv"


# ---------------------------------------------------------------- 라벨 변환
def load_label_map(path=LABEL_MAP):
    table = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            table[(r["source"].strip(), r["source_label"].strip())] = (
                r["subcategory"].strip() or None, r["main_category"].strip() or None
            )
    return table


def map_label(table, source, label):
    """(세부분류 or None, 대분류 or None). 매핑이 없으면 (None, None)."""
    hit = table.get((source, label))
    if hit is None and "/" in label:  # utzappos: 'Boots/Ankle' → 'Boots'
        hit = table.get((source, label.split("/")[0]))
    if hit is not None:
        return hit
    sub = canonical_subcategory(label)
    if sub in SUBCATEGORY_TO_MAIN:
        return sub, SUBCATEGORY_TO_MAIN[sub]
    if label in MAIN_CATEGORIES:
        return None, label
    return None, None


# ---------------------------------------------------------------- 데이터셋별 읽기
# 각 함수는 (이미지 경로, 원본 라벨, bbox 또는 None, split 또는 None)을 내보낸다.
def _find(root, name):
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def _rows(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return [ln.split() for ln in lines[2:] if ln.strip()]  # 1행: 개수, 2행: 헤더


def read_deepfashion(root):
    """DeepFashion Category and Attribute Prediction Benchmark."""
    cloth_f, img_f = _find(root, "list_category_cloth.txt"), _find(root, "list_category_img.txt")
    if not cloth_f or not img_f:
        raise SystemExit("list_category_cloth.txt / list_category_img.txt 를 찾지 못했습니다.")
    names = [r[0] for r in _rows(cloth_f)]
    bbox_f, part_f = _find(root, "list_bbox.txt"), _find(root, "list_eval_partition.txt")
    bboxes = {r[0]: tuple(map(int, r[1:5])) for r in _rows(bbox_f)} if bbox_f else {}
    parts = {r[0]: ("train" if r[1] == "train" else "val") for r in _rows(part_f)} if part_f else {}
    for r in _rows(img_f):
        rel, idx = r[0], int(r[1])
        path = root / rel
        if not path.exists():
            path = img_f.parent.parent / rel
        yield path, names[idx - 1], bboxes.get(rel), parts.get(rel)


def read_deepfashion2(root):
    for split_dir, split in (("train", "train"), ("validation", "val")):
        anno_dir = root / split_dir / "annos"
        if not anno_dir.exists():
            continue
        for anno in sorted(anno_dir.glob("*.json")):
            data = json.loads(anno.read_text(encoding="utf-8"))
            img = root / split_dir / "image" / f"{anno.stem}.jpg"
            for key, item in data.items():
                if key.startswith("item") and isinstance(item, dict):
                    yield img, item["category_name"], tuple(item["bounding_box"]), split


def read_utzappos(root):
    if not (root / "Shoes").exists():
        cand = [d for d in root.iterdir() if d.is_dir() and (d / "Shoes").exists()]
        if cand:
            root = cand[0]
    for path in sorted(root.rglob("*.jpg")):
        parts = path.relative_to(root).parts
        if len(parts) >= 3:
            yield path, f"{parts[0]}/{parts[1]}", None, None


def read_csv(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            path = Path(r["image_path"])
            if not path.is_absolute():
                path = csv_path.parent / path
            bbox = None
            if all(r.get(k) not in (None, "") for k in ("x1", "y1", "x2", "y2")):
                bbox = tuple(int(float(r[k])) for k in ("x1", "y1", "x2", "y2"))
            split = (r.get("split") or "").strip() or None
            yield path, r["label"].strip(), bbox, ("train" if split == "train" else "val") if split else None


# ---------------------------------------------------------------- 메인
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, choices=["deepfashion", "deepfashion2", "utzappos", "csv"])
    p.add_argument("--root", type=Path, help="데이터셋 폴더 (csv 제외)")
    p.add_argument("--csv", type=Path, help="--source csv 일 때 라벨 csv")
    p.add_argument("--name", default=None, help="csv 출처 이름 (라벨 변환표의 source 값, 기본: aihub)")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--task", choices=["sub", "main"], default="sub")
    p.add_argument("--val-ratio", type=float, default=0.15)
    p.add_argument("--max-per-class", type=int, default=0, help="클래스당 최대 장수 (0=제한 없음)")
    p.add_argument("--min-size", type=int, default=64, help="이보다 작은 이미지/크롭은 버림")
    p.add_argument("--min-report", type=int, default=200, help="이보다 적은 클래스를 '부족'으로 표시")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dry-run", action="store_true", help="저장 없이 집계만")
    p.add_argument("--label-map", type=Path, default=LABEL_MAP)
    args = p.parse_args(argv)

    if args.source == "csv":
        if not args.csv:
            p.error("--source csv 에는 --csv 가 필요합니다.")
        source_name, records = args.name or "aihub", read_csv(args.csv)
    else:
        if not args.root:
            p.error("--root 가 필요합니다.")
        source_name = args.source
        records = {"deepfashion": read_deepfashion, "deepfashion2": read_deepfashion2,
                   "utzappos": read_utzappos}[args.source](args.root)

    table = load_label_map(args.label_map)
    rng = random.Random(args.seed)
    saved, unmapped, main_only = collections.Counter(), collections.Counter(), collections.Counter()
    skipped = collections.Counter()

    for i, (path, label, bbox, split) in enumerate(records):
        sub, main_cat = map_label(table, source_name, label)
        cls = sub if args.task == "sub" else main_cat
        if cls is None:
            (main_only if (args.task == "sub" and main_cat) else unmapped)[label] += 1
            continue
        if args.max_per_class and saved[cls] >= args.max_per_class:
            continue
        if not path.exists():
            skipped["이미지 없음"] += 1
            continue
        split = split or ("val" if rng.random() < args.val_ratio else "train")
        if not args.dry_run:
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    if bbox:
                        x1, y1, x2, y2 = bbox
                        im = im.crop((max(0, x1), max(0, y1), min(im.width, x2), min(im.height, y2)))
                    if min(im.size) < args.min_size:
                        skipped["너무 작음"] += 1
                        continue
                    dst = args.out / split / cls / f"{source_name}_{i:07d}_{path.stem}.jpg"
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    im.save(dst, quality=92)
            except OSError:
                skipped["읽기 실패"] += 1
                continue
        saved[cls] += 1

    print(f"\n[{source_name}] 저장 {sum(saved.values())}장 → {args.out}")
    for cls, n in sorted(saved.items(), key=lambda x: -x[1]):
        print(f"  {cls}: {n}")
    if main_only:
        print(f"\n대분류만 있는 라벨 (--task sub 에서는 제외, --task main 에서 사용): {dict(main_only)}")
    if unmapped:
        print("\n매핑 안 된 라벨 (data/dataset_label_map.csv 에 행을 추가하세요):")
        for lb, n in unmapped.most_common(30):
            print(f"  {lb}: {n}")
    if skipped:
        print(f"\n건너뜀: {dict(skipped)}")

    # 출력 폴더 전체 기준 클래스별 장수 (여러 출처 누적)
    classes = list(SUBCATEGORY_TO_MAIN) if args.task == "sub" else MAIN_CATEGORIES
    totals = {c: sum(1 for _ in (args.out / "train" / c).glob("*.jpg")) if (args.out / "train" / c).exists() else 0
              for c in classes}
    short = {c: n for c, n in totals.items() if n < args.min_report}
    if not args.dry_run:
        print(f"\n누적 train 장수가 {args.min_report}장 미만인 클래스 ({len(short)}개):")
        for c, n in sorted(short.items(), key=lambda x: x[1]):
            print(f"  {c}: {n}")
    return {"saved": dict(saved), "unmapped": dict(unmapped), "main_only": dict(main_only),
            "skipped": dict(skipped), "totals": totals}


if __name__ == "__main__":
    main()
