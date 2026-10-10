# AI 옷장

## 실행 방법

Python 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt
pip install -r requirements-ai.txt
streamlit run app.py
```

- 실행하면 브라우저에서 `http://localhost:8501` 이 열립니다.
- `requirements-ai.txt` 는 사진 AI 분류용(PyTorch)입니다. 설치하지 않으면 옷 종류를 직접 골라야 합니다.
  GPU가 없는 PC는 아래 명령이 더 가볍습니다.

  ```bash
  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
  ```

- Windows에서는 설치 후 `run_windows.bat` 을 더블클릭해도 됩니다.
- Google Colab에서는 `COLAB_RUN.ipynb` 를 열고 셀을 순서대로 실행합니다.

## 체험판 웹 페이지

설치 없이 브라우저에서 돌아가는 체험판이 `web/` 에 있습니다 (추천 + 사진 AI 분류, 날씨는 직접 입력).

```bash
python -m http.server -d web 8000
```

브라우저에서 `http://localhost:8000` 을 엽니다. 분류기를 다시 학습했다면 `python scripts/build_web.py` 로 페이지를 다시 만듭니다.

## 데이터 출처

옷 사진 분류기 학습에 쓴 공개 데이터셋 (총 34,137장, 42종 · 받는 방법과 라벨 규칙은 `scripts/open_datasets.py`)

- [KREAM 상품 이미지](https://huggingface.co/datasets/hahminlew/kream-product-blip-captions) (CC BY-NC-SA 4.0) — 한국 리셀 플랫폼 KREAM의 상의·하의·아우터 상품 사진, 상품명 키워드로 종류를 나눠 7,798장 사용
- [Fashion Product Images (Myntra)](https://huggingface.co/datasets/benitomartin/fashion-product-images-small-384x512) (Kaggle 원본 MIT) — 인도 쇼핑몰 Myntra의 의류·신발 상품 사진, 상품 유형과 이름으로 6,496장 사용
- [Fashion200k](https://huggingface.co/datasets/Marqo/fashion200k) (Apache-2.0 표기) — 여성복 쇼핑몰 사진 연구용 데이터, 원피스·재킷·레깅스 등 5,245장 사용 (같은 상품의 다른 컷은 1장만)
- [clothing-dataset](https://github.com/alexeygrigorev/clothing-dataset) (CC0) — 사람들이 휴대폰으로 직접 찍은 옷 사진, 앱 사용 환경과 가장 비슷해 정확도 확인용으로도 쓰며 1,955장 사용
- [UT-Zappos50K](https://vision.cs.utexas.edu/projects/finegrained/utzap50k/) (연구용) — 미국 신발 쇼핑몰 Zappos의 신발 사진 5만 장, 운동화·구두·힐·샌들 등 5,400장 사용
- [shoe-classification](https://huggingface.co/datasets/keremberke/shoe-classification) (Public Domain) — 나이키·아디다스·컨버스 신발 사진, 컨버스 사진을 캔버스화로 237장 사용
- [H&M 상품 이미지 128px](https://huggingface.co/datasets/multabench/core-img-reg-hnm-fashion) (Kaggle H&M 추천 대회 데이터, 비상업 연구용) — H&M 상품 사진과 설명, 후드집업·플리스·터틀넥 등을 보충하려고 7,006장 사용 (아동복 제외)

그 밖에 쓰는 데이터

- [ImageNet 사전학습 EfficientNet-B0](https://pytorch.org/vision/stable/models/generated/torchvision.models.efficientnet_b0.html) (torchvision) — 분류기의 출발점이 되는 사전학습 모델, 이 위에 위 사진들로 추가 학습
- [Open-Meteo](https://open-meteo.com/) (비상업 무료, 데이터 CC BY 4.0) — 앱에서 현재 기온·체감온도·습도·강수·풍속을 불러오는 날씨 API
