# 이전 버전 보관 (공개 배포 작업 전)

`AI옷장_배포전_74c3210.zip` 은 공개 배포(로그인·Supabase·가상 착용 Space) 작업을 하기 직전의 저장소 전체입니다.

- 기준 커밋: `74c3210` (CatVTON 가상 착용 시험용 Colab 노트북 추가)
- 로그인 없이 내 PC에서만 도는 버전입니다 (SQLite + `data/uploads/`).
- GitHub 에서 같은 버전을 바로 받을 수도 있습니다: <https://github.com/kds020902/AI_capstone/archive/74c3210.zip>

실행 방법

```bash
# 압축을 푼 뒤
cd AI_capstone_before_deploy
pip install -r requirements.txt
pip install -r requirements-ai.txt   # 사진 AI 분류 (선택)
streamlit run app.py
```

참고: 지금 버전도 Supabase 설정을 넣지 않으면 이 버전과 똑같이 로컬 모드(로그인 없음)로 동작합니다.
