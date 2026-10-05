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
