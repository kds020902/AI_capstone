"""EfficientNet-B0 의류 분류기 학습.

데이터 폴더는 scripts/prepare_dataset.py 로 만든다.
  dataset_sub/train/반팔 티셔츠, 긴팔 티셔츠, ... 플랫슈즈      (42종)

사용 예
  python scripts/train_classifier.py --data dataset_sub --epochs 10
  python scripts/train_classifier.py --data dataset_sub --epochs 10 --unfreeze-blocks 2
  python scripts/train_classifier.py --data dataset_sub --cache-features --unfreeze-blocks 2 --epochs 15   # GPU 없을 때

동작
- 학습 전 클래스명 검사: 규칙표에 없는 클래스 폴더가 있으면 중단
- train/val 클래스 목록이 다르면 중단: ImageFolder는 폴더 기준으로 번호를 매겨서,
  val에 클래스 하나만 빠져도 번호가 어긋나 정확도가 엉터리로 계산된다
- --unfreeze-blocks: 비슷한 세부분류(셔츠/블라우스 등)를 구분하려면 마지막 블록 몇 개를 같이 학습
- 최고 성능 epoch의 클래스별 정확도·혼동행렬·출처별 정확도를 models/에 CSV로 저장
- --cache-features: 사전학습 backbone으로 이미지마다 특징을 '한 번만' 뽑아 저장하고 뒷부분만 학습.
  CPU에서도 수만 장을 한 시간 안에 학습할 수 있다 (좌우 반전본도 같이 뽑아 증강).
  --unfreeze-blocks 2 와 같이 쓰면 마지막 블록 2개도 함께 학습한다 (중간 특징 맵을 저장).
  클래스 수 불균형은 손실 가중치(1/√장수)로 보정하고, 클래스 평균 정확도(macro)로 최고 epoch를 고른다.
  저장 형식은 일반 학습과 같아 앱(core/vision.py)이 그대로 읽는다.
"""
from pathlib import Path
import argparse
import csv
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.taxonomy import SUBCATEGORY_TO_MAIN  # noqa: E402

TASK = "subcategory"  # 체크포인트에 같이 저장 (앱이 모델 종류를 표시할 때 씀)


def check_classes(train_classes, val_classes):
    known = set(SUBCATEGORY_TO_MAIN)
    unknown = [c for c in train_classes if c not in known]
    if unknown:
        raise SystemExit(f"규칙표(data/clothing_taxonomy.csv)에 없는 클래스 폴더: {unknown}")
    if list(train_classes) != list(val_classes):
        missing = sorted(set(train_classes) ^ set(val_classes))
        raise SystemExit(f"train/val 클래스 폴더가 다릅니다 (번호가 어긋남): {missing}")


def accuracies(confusion):
    """(전체 정확도, 클래스 평균 정확도). 클래스 평균은 장수가 적은 클래스도 똑같이 센다."""
    k = len(confusion)
    total = sum(map(sum, confusion))
    acc = sum(confusion[i][i] for i in range(k)) / max(total, 1)
    per = [confusion[i][i] / sum(confusion[i]) for i in range(k) if sum(confusion[i])]
    return acc, (sum(per) / len(per) if per else 0.0)


def save_reports(models_dir, classes, confusion):
    with open(models_dir / "classifier_per_class.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["class", "n_val", "accuracy"])
        for i, c in enumerate(classes):
            n_i = sum(confusion[i])
            w.writerow([c, n_i, f"{confusion[i][i] / n_i:.4f}" if n_i else ""])
    with open(models_dir / "classifier_confusion.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["정답\\예측"] + list(classes))
        for i, c in enumerate(classes):
            w.writerow([c] + confusion[i])


def save_source_report(models_dir, ds, truth, pred):
    """출처별 val 정확도. prepare_dataset.py 가 파일명 앞에 붙인 출처(kream_..., grigorev_...)로 나눈다.
    폰 사진(grigorev)과 상품컷(kream, fpi …)의 차이를 보려는 것."""
    by = {}
    for (path, _), t, p in zip(ds.samples, truth, pred):
        src = Path(path).name.split("_", 1)[0]
        n, ok = by.get(src, (0, 0))
        by[src] = (n + 1, ok + (t == p))
    with open(models_dir / "classifier_by_source.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["source", "n_val", "accuracy"])
        for src, (n, ok) in sorted(by.items()):
            w.writerow([src, n, f"{ok / n:.4f}"])
            print(f"  {src}: {ok / n:.3f} ({n}장)")


def extract_features(encode, ds, batch_size, flip, cache_path):
    """ImageFolder 전체를 encode(고정된 앞부분)에 통과시킨 (특징, 라벨). 같은 파일 목록의 캐시가 있으면 재사용."""
    import torch
    from torch.utils.data import DataLoader

    files = [p for p, _ in ds.samples]
    if cache_path.exists():
        cached = torch.load(cache_path)
        if cached["files"] == files:
            return cached["x"], cached["y"]
    loader = DataLoader(ds, batch_size=batch_size, num_workers=2)
    xs, ys, done = [], [], 0
    with torch.no_grad():
        for x, y in loader:
            if flip:
                x = torch.flip(x, dims=[3])
            xs.append(encode(x))
            ys.append(y)
            done += len(y)
            if done % (batch_size * 20) < batch_size:
                print(f"  특징 추출 {done}/{len(ds)}", flush=True)
    x, y = torch.cat(xs), torch.cat(ys)
    torch.save({"files": files, "x": x, "y": y}, cache_path)
    return x, y


def train_cached(args, train_ds, val_ds, out, models_dir):
    """backbone 앞부분은 고정하고 출력만 한 번 저장해 둔 뒤, 뒷부분(--unfreeze-blocks 개 블록) + 분류층만 학습.
    unfreeze 0 이면 1280차원 풀링 특징 + 분류층, 2 이면 192×7×7 특징 맵(fp16) + 마지막 블록 2개 + 분류층."""
    import copy
    import torch
    from torch import nn
    from torchvision import models

    torch.manual_seed(args.seed)
    model = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.DEFAULT).eval()
    n_free = max(0, args.unfreeze_blocks)
    split = len(model.features) - n_free
    frozen, tail = model.features[:split], model.features[split:]

    def encode(x):
        f = frozen(x)
        return torch.flatten(model.avgpool(f), 1) if n_free == 0 else f.half()

    cache_dir = Path(args.data) / ".feature_cache"
    cache_dir.mkdir(exist_ok=True)
    tag = "" if n_free == 0 else f"_b{n_free}"
    print("train 특징 추출 (원본)")
    xtr, ytr = extract_features(encode, train_ds, args.batch_size, False, cache_dir / f"train{tag}.pt")
    if args.flip_aug:
        print("train 특징 추출 (좌우 반전)")
        xf, yf = extract_features(encode, train_ds, args.batch_size, True, cache_dir / f"train_flip{tag}.pt")
        xtr, ytr = torch.cat([xtr, xf]), torch.cat([ytr, yf])
    print("val 특징 추출")
    xva, yva = extract_features(encode, val_ds, args.batch_size, False, cache_dir / f"val{tag}.pt")

    k = len(train_ds.classes)
    counts = torch.bincount(ytr, minlength=k).float()
    class_w = (1.0 / counts.clamp(min=1).sqrt())
    class_w = class_w / class_w.mean()
    in_features = model.classifier[1].in_features
    head = nn.Sequential(nn.Dropout(0.3), nn.Linear(in_features, k))

    def forward(xb):
        if n_free:
            xb = torch.flatten(model.avgpool(tail(xb.float())), 1)
        return head(xb)

    groups = [{"params": head.parameters(), "lr": args.lr}]
    if n_free:
        groups.append({"params": tail.parameters(), "lr": args.lr * 0.1})
    opt = torch.optim.AdamW(groups, weight_decay=1e-3)
    loss_fn = nn.CrossEntropyLoss(weight=class_w, label_smoothing=0.1)
    batch = 256 if n_free == 0 else 128
    best, best_acc, best_conf, best_pred = -1.0, 0.0, None, None
    best_head = best_tail = None
    for epoch in range(args.epochs):
        head.train()
        tail.train()
        perm = torch.randperm(len(ytr))
        for i in range(0, len(perm), batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            loss_fn(forward(xtr[idx]), ytr[idx]).backward()
            opt.step()
        head.eval()
        tail.eval()
        with torch.no_grad():
            pred = torch.cat([forward(xva[i:i + 512]).argmax(1) for i in range(0, len(yva), 512)])
        confusion = [[0] * k for _ in range(k)]
        for t, pr in zip(yva.tolist(), pred.tolist()):
            confusion[t][pr] += 1
        acc, macro = accuracies(confusion)
        if macro > best:
            best, best_acc, best_conf, best_pred = macro, acc, confusion, pred
            best_head = copy.deepcopy(head.state_dict())
            best_tail = copy.deepcopy(tail.state_dict()) if n_free else None
        if n_free or (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"epoch={epoch+1} val_acc={acc:.4f} val_macro={macro:.4f}", flush=True)

    # 학습한 부분을 EfficientNet-B0 원래 구조에 넣어 앱(core/vision.py)과 같은 형식으로 저장
    if n_free:
        tail.load_state_dict(best_tail)
    model.classifier[1] = nn.Linear(in_features, k)
    model.classifier[1].load_state_dict({"weight": best_head["1.weight"], "bias": best_head["1.bias"]})
    torch.save({"state_dict": model.state_dict(), "classes": train_ds.classes, "task": TASK,
                "val_accuracy": best_acc, "val_macro_accuracy": best,
                "mode": f"cached-features, unfreeze={n_free}"}, out)
    save_reports(models_dir, train_ds.classes, best_conf)
    save_source_report(models_dir, val_ds, yva.tolist(), best_pred.tolist())
    print(f"saved: {out}  (val_acc={best_acc:.4f}, val_macro={best:.4f})")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--unfreeze-blocks", type=int, default=0,
                   help="backbone 마지막 N개 블록도 학습 (0=분류층만, 기존 방식)")
    p.add_argument("--cache-features", action="store_true",
                   help="특징을 한 번만 뽑아 두고 분류층만 학습 (GPU 없을 때). --epochs 는 100 정도 권장")
    p.add_argument("--no-flip-aug", dest="flip_aug", action="store_false", help="--cache-features 에서 좌우 반전 증강 끄기")
    p.add_argument("--out", type=Path, default=None, help="체크포인트 경로 (기본: models/clothing_classifier.pt)")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    import torch
    from torch import nn
    from torch.utils.data import DataLoader
    from torchvision import datasets, transforms, models

    norm = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    root = Path(args.data)
    plain = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(), norm])
    train_ds = datasets.ImageFolder(root / "train", transform=plain if args.cache_features else transforms.Compose([
        transforms.Resize((224, 224)), transforms.RandomHorizontalFlip(), transforms.RandomRotation(8),
        transforms.ToTensor(), norm,
    ]))
    val_ds = datasets.ImageFolder(root / "val", transform=plain)
    check_classes(train_ds.classes, val_ds.classes)

    models_dir = ROOT / "models"
    models_dir.mkdir(exist_ok=True)
    out = args.out or models_dir / "clothing_classifier.pt"
    if args.cache_features:
        return train_cached(args, train_ds, val_ds, out, out.parent)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = models.efficientnet_b0(weights=models.EfficientNet_B0_Weights.DEFAULT)
    for param in model.features.parameters():
        param.requires_grad = False
    if args.unfreeze_blocks > 0:
        for block in list(model.features)[-args.unfreeze_blocks:]:
            for param in block.parameters():
                param.requires_grad = True
    n = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(n, len(train_ds.classes))
    model.to(device)

    head = [p for p in model.classifier.parameters() if p.requires_grad]
    body = [p for p in model.features.parameters() if p.requires_grad]
    groups = [{"params": head, "lr": args.lr}]
    if body:
        groups.append({"params": body, "lr": args.lr * 0.1})
    optimizer = torch.optim.AdamW(groups)
    loss_fn = nn.CrossEntropyLoss()

    k = len(train_ds.classes)
    best = -1.0

    for epoch in range(args.epochs):
        model.train()
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            optimizer.step()

        model.eval()
        confusion = [[0] * k for _ in range(k)]
        with torch.no_grad():
            for x, y in val_loader:
                pred = model(x.to(device)).argmax(1).cpu()
                for t, pr in zip(y.tolist(), pred.tolist()):
                    confusion[t][pr] += 1
        acc, macro = accuracies(confusion)
        print(f"epoch={epoch+1} val_acc={acc:.4f} val_macro={macro:.4f}")

        if acc > best:
            best = acc
            torch.save({
                "state_dict": model.state_dict(), "classes": train_ds.classes,
                "task": TASK, "val_accuracy": acc, "val_macro_accuracy": macro,
            }, out)
            save_reports(out.parent, train_ds.classes, confusion)
            print("saved:", out)


if __name__ == "__main__":
    main()
