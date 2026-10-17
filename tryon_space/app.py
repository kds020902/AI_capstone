"""AI 옷장 · 가상 착용(예시) — Hugging Face ZeroGPU Space 용 Gradio 앱.

외부 공개 사전학습 모델 CatVTON(https://github.com/Zheng-Chong/CatVTON, CC BY-NC-SA 4.0)을 그대로 쓴다.
AI 옷장에서 직접 학습한 모델이 아니다. 비상업·학습·시연용.

- 사람 사진 + 옷 사진(최대 2벌: 상의·원피스·아우터 → 하의 순서) → 그 옷을 입은 예시 이미지
- AI 옷장 앱이 "?garment=<서명 주소>&kind=upper&garment2=...&kind2=lower" 로 옷 사진을 넘길 수 있다.
  허용한 호스트(ALLOWED_GARMENT_HOSTS, 기본 *.supabase.co)의 https 주소만 받는다.
- 사람 사진과 결과는 저장하지 않는다. Gradio 임시 파일은 30분마다 정리해 1시간이 넘은 것을 지운다 (delete_cache).
- 무료 GPU(ZeroGPU): 방문자 계정 기준 하루 사용량(비로그인 약 2분, 로그인 약 5분)과 대기열이 있다.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
import numpy as np
import requests
import spaces
import torch
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
CATVTON_DIR = Path(os.environ.get("CATVTON_DIR", HERE / "CatVTON"))
CATVTON_COMMIT = "7818397"     # 2025-12-16 판 (Colab 시험과 같은 코드)
if not (CATVTON_DIR / "model" / "pipeline.py").exists():
    subprocess.run(["git", "clone", "-q", "https://github.com/Zheng-Chong/CatVTON.git", str(CATVTON_DIR)], check=True)
    subprocess.run(["git", "-C", str(CATVTON_DIR), "checkout", "-q", CATVTON_COMMIT], check=True)
sys.path.insert(0, str(CATVTON_DIR))

import detectron2.config.defaults as d2_defaults  # noqa: E402
from diffusers.image_processor import VaeImageProcessor  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from model.cloth_masker import AutoMasker  # noqa: E402
from model.pipeline import CatVTONPipeline  # noqa: E402
from utils import resize_and_crop, resize_and_padding  # noqa: E402

ZERO_GPU = os.environ.get("SPACES_ZERO_GPU", "").lower() in ("1", "true")
DEVICE = "cuda" if (ZERO_GPU or torch.cuda.is_available()) else "cpu"
SIZE = tuple(int(v) for v in os.environ.get("TRYON_SIZE", "768x1024").split("x"))   # (가로, 세로)
SEC_PER_STEP = float(os.environ.get("TRYON_SEC_PER_STEP", "1.0"))   # GPU 예약 시간 계산용 (넉넉하게)
ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get("ALLOWED_GARMENT_HOSTS", ".supabase.co").split(",") if h.strip()]
ALLOW_HTTP = os.environ.get("ALLOW_HTTP_GARMENT") == "1"     # 로컬 시험 전용
MAX_GARMENT_BYTES = 10 * 1024 * 1024
KINDS = {"상의": "upper", "하의": "lower", "원피스·전신": "overall", "아우터": "outer"}
KIND_LABEL = {v: k for k, v in KINDS.items()}

# ------------------------------------------------------------ 모델 불러오기 (ZeroGPU는 시작할 때 cuda에 올려 둔다)
d2_defaults._C.MODEL.DEVICE = DEVICE
cat_dir = snapshot_download("zhengchong/CatVTON", allow_patterns=["DensePose/*", "SCHP/*", "mix-48k-1024/*"])
base_dir = snapshot_download("booksforcharlie/stable-diffusion-inpainting",
                             allow_patterns=["model_index.json", "scheduler/*", "unet/*", "feature_extractor/*",
                                             "safety_checker/*"])
pipeline = CatVTONPipeline(base_ckpt=base_dir, attn_ckpt=cat_dir, attn_ckpt_version="mix",
                           weight_dtype=torch.bfloat16 if DEVICE == "cuda" else torch.float32,
                           use_tf32=True, device=DEVICE)
pipeline.skip_safety_check = True        # 안전 필터는 아래에서 직접 돌려 알아듣기 쉬운 안내로 바꾼다
automasker = AutoMasker(densepose_ckpt=os.path.join(cat_dir, "DensePose"), schp_ckpt=os.path.join(cat_dir, "SCHP"),
                        device=DEVICE)
mask_blur = VaeImageProcessor(vae_scale_factor=8, do_normalize=False, do_binarize=True, do_convert_grayscale=True)


class TryOnError(Exception):
    """사용자에게 그대로 보여 줄 안내."""


def _one_pass(person, cloth, kind, steps, seed):
    person = resize_and_crop(person, SIZE)
    cloth = resize_and_padding(cloth, SIZE)
    parts = automasker(person, kind)
    if (np.array(parts["densepose"]) > 0).mean() < 0.005:      # 몸 부위가 거의 안 잡히면 사람이 없는 사진
        raise TryOnError("사진에서 사람을 찾지 못했어요. 몸이 잘 보이게 다시 찍어 주세요.")
    mask = mask_blur.blur(parts["mask"], blur_factor=9)
    gen = torch.Generator(device=DEVICE).manual_seed(int(seed))
    result = pipeline(image=person, condition_image=cloth, mask=mask, num_inference_steps=int(steps),
                      guidance_scale=2.5, generator=gen, width=SIZE[0], height=SIZE[1])[0]
    if pipeline.run_safety_checker(image=np.array(result)[None])[1][0]:
        raise TryOnError("결과가 안전 필터에 걸렸어요. 다른 사진으로 다시 해 보세요.")
    return result


def _gpu_seconds(person, garment, kind, garment2, kind2, steps, seed):
    passes = 1 + (garment2 is not None)
    return int(min(120, 20 + passes * int(steps) * SEC_PER_STEP))


@spaces.GPU(duration=_gpu_seconds)
def _run_gpu(person, garment, kind, garment2, kind2, steps, seed):
    """GPU 작업. 안내 문구는 예외 대신 값으로 돌려준다 (ZeroGPU 작업 프로세스 밖으로 확실히 전달되게)."""
    t0 = time.time()
    try:
        out = _one_pass(person, garment, kind, steps, seed)
        if garment2 is not None:
            out = _one_pass(out, garment2, kind2, steps, seed)
        return out, None
    except TryOnError as e:
        return None, str(e)
    finally:   # TRYON_SEC_PER_STEP 을 맞출 때 참고 (Space 의 Logs 탭에 보임)
        print(f"GPU 사용 {time.time() - t0:.1f}초 · {1 + (garment2 is not None)}벌 · {steps}단계", flush=True)


def _gpu_failure_message(err):
    """ZeroGPU 가 내는 영어 오류(할당량 초과·시간 초과 등)를 알아듣기 쉬운 안내로 바꾼다. 원문도 붙여 둔다."""
    text = str(getattr(err, "message", "") or err)
    low = (str(getattr(err, "title", "") or "") + " " + text).lower()   # spaces 는 제목(title)에 종류를 적는다
    if "quota" in low or "gpu limit" in low:
        return ("오늘 쓸 수 있는 무료 GPU 사용량을 다 썼어요. 첫 사용 24시간 뒤에 다시 채워져요. "
                "Hugging Face에 로그인해 Space 페이지(새 창)에서 쓰면 사용량이 더 많아요. (" + text + ")")
    if "illegal duration" in low:
        return "요청한 GPU 시간이 무료 한도보다 길어요. 품질을 낮추거나 옷을 한 벌만 넣어 주세요. (" + text + ")"
    if "timeout" in low or "timed out" in low or "aborted" in low:
        return "GPU 작업 시간이 넘었어요. 품질을 '빠르게'로 바꾸거나 옷을 한 벌만 넣고 다시 해 보세요. (" + text + ")"
    return "가상 착용 중 문제가 생겼어요. 잠시 뒤 다시 해 보세요. (" + text + ")"


def try_on(person, garment, kind_label, garment2, kind2_label, steps, seed=42, progress=gr.Progress()):
    if person is None:
        raise gr.Error("사람 사진을 올리거나 찍어 주세요.")
    if garment is None and garment2 is None:
        raise gr.Error("입혀 볼 옷 사진을 하나 이상 넣어 주세요.")
    if garment is None:
        garment, kind_label, garment2 = garment2, kind2_label, None
    progress(0, desc="실행 중 · GPU 차례를 기다렸다가 옷을 입히는 중… (보통 수십 초, 무료 GPU라 더 걸릴 수 있어요)")
    t0 = time.time()
    try:
        result, problem = _run_gpu(ImageOps.exif_transpose(person).convert("RGB"),
                                   ImageOps.exif_transpose(garment).convert("RGB"), KINDS[kind_label],
                                   ImageOps.exif_transpose(garment2).convert("RGB") if garment2 is not None else None,
                                   KINDS[kind2_label], int(steps), seed)
    except Exception as e:      # 할당량 초과·시간 초과 등 ZeroGPU 쪽 실패
        raise gr.Error(_gpu_failure_message(e), duration=None) from e
    if problem:
        raise gr.Error(problem)
    note = f"완료 · {time.time() - t0:.0f}초 (대기 포함) · AI가 그린 예시 이미지예요. 실제 사이즈와 핏은 반영되지 않아요."
    return result, note


# ------------------------------------------------------------ AI 옷장에서 넘긴 옷 사진 받기
def _allowed(url):
    """https 이고, 호스트가 허용 목록과 같거나(예: abc.supabase.co) '.'으로 시작하는 항목의 하위 도메인일 때만."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" and not (ALLOW_HTTP and p.scheme == "http"):
        return False
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in ALLOWED_HOSTS)


def _fetch(url):
    if not url:
        return None
    if not _allowed(url):
        raise gr.Error("허용하지 않은 주소의 옷 사진이에요. 옷 사진을 직접 올려 주세요.")
    r = requests.get(url, timeout=15, stream=True)
    r.raise_for_status()
    data = r.raw.read(MAX_GARMENT_BYTES + 1, decode_content=True)
    if len(data) > MAX_GARMENT_BYTES:
        raise gr.Error("옷 사진이 너무 커요 (10MB 넘음).")
    return Image.open(io.BytesIO(data)).convert("RGB")


def load_from_app(request: gr.Request):
    q = dict(request.query_params) if request else {}
    g1, g2, msg = None, None, ""
    try:
        g1, g2 = _fetch(q.get("garment")), _fetch(q.get("garment2"))
        if g1 is not None or g2 is not None:
            msg = "AI 옷장에서 옷 사진을 가져왔어요. 내 사진만 올리고 '입혀 보기'를 누르세요."
    except gr.Error as e:
        msg = str(e)
    except Exception:
        msg = "옷 사진을 가져오지 못했어요 (주소가 만료됐을 수 있어요). 옷 사진을 직접 올려 주세요."
    k1 = KIND_LABEL.get(q.get("kind"), "상의")
    k2 = KIND_LABEL.get(q.get("kind2"), "하의")
    return g1, k1, g2, k2, msg


with gr.Blocks(title="AI 옷장 · 가상 착용(예시)", delete_cache=(1800, 3600)) as demo:
    gr.Markdown("## 👗 AI 옷장 · 가상 착용(예시)\n"
                "사람 사진에 옷 사진을 입혀 보는 **AI 예시 이미지**를 만들어요. 실제 사이즈·핏은 반영되지 않아요. "
                "외부 공개 모델 [CatVTON](https://github.com/Zheng-Chong/CatVTON)(비상업 라이선스)을 사용해요.")
    info = gr.Markdown()
    with gr.Row():
        with gr.Column():
            person = gr.Image(label="1. 내 사진 (정면, 허리 아래까지, 팔을 몸에서 살짝 떼고)", type="pil",
                              sources=["upload", "webcam"], height=360)
        with gr.Column():
            garment = gr.Image(label="2. 입혀 볼 옷 (옷만 펼쳐 찍은 사진이 잘 나와요)", type="pil", height=260)
            kind = gr.Dropdown(list(KINDS), value="상의", label="옷 종류")
            with gr.Accordion("두 번째 옷 (선택, 예: 하의)", open=False):
                garment2 = gr.Image(label="두 번째 옷", type="pil", height=200)
                kind2 = gr.Dropdown(list(KINDS), value="하의", label="두 번째 옷 종류")
    steps = gr.Radio([("빠르게 (20단계)", 20), ("보통 (30단계)", 30), ("최고 (50단계)", 50)], value=30, label="품질")
    run = gr.Button("입혀 보기", variant="primary")
    with gr.Row():
        result = gr.Image(label="결과 (AI 예시 이미지)", type="pil", height=480, format="jpeg")
    status = gr.Markdown()
    gr.Markdown("사람 사진과 결과는 저장하지 않아요 (임시 파일은 1시간이 지나면 자동 삭제). "
                "무료 GPU라 대기열이 있고, 방문자별 하루 사용량(비로그인 약 2분, Hugging Face 로그인 시 약 5분)을 "
                "다 쓰면 다음 날 다시 쓸 수 있어요. 사진에 사람이 없거나 안전 필터에 걸리면 다시 시도하라고 알려 드려요.")
    run.click(try_on, [person, garment, kind, garment2, kind2, steps], [result, status], concurrency_limit=1)
    demo.load(load_from_app, None, [garment, kind, garment2, kind2, info])

if __name__ == "__main__":
    demo.queue(max_size=20).launch()
