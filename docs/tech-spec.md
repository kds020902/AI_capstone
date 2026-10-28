# 기술설계서 — AI 옷장

작성 기준: 2026-10-20, `main` 커밋 `1b1c6f1`. 기능 기준은 `docs/prd.md`, 배포 절차는 `DEPLOY.md`.

## 1. 기술 스택

| 영역 | 사용 기술 | 버전 |
|---|---|---|
| 앱 화면·서버 | Python, Streamlit | Python 3.12 (배포), Streamlit 1.66.0 |
| 로그인·DB·파일 | Supabase (Auth, PostgreSQL + RLS, Storage), supabase-py | supabase 2.33.0 |
| 옷 분류 AI | PyTorch, torchvision (EfficientNet-B0) | torch 2.9.1 CPU, torchvision 0.24.1 CPU |
| 데이터 처리 | pandas, numpy, Pillow | requirements.txt 범위 |
| 날씨 | Open-Meteo API (requests) | – |
| 가상 착용 | Hugging Face ZeroGPU Space, Gradio, spaces, diffusers, transformers, CatVTON | Gradio 5.49.1, diffusers 0.41.0, transformers 5.19.0, CatVTON 커밋 7818397 |
| 웹 체험판 | 순수 HTML·JavaScript (라이브러리 없음) | – |
| 로컬 모드 | SQLite, 로컬 폴더 `data/uploads/` | – |
| 시험 | pytest, Streamlit AppTest, Node.js(웹-앱 결과 비교) | – |

## 2. 시스템 구조

```
[사용자 브라우저]
   │ https
   ▼
[Streamlit Community Cloud]  app.py + core/
   ├─ core/vision.py        옷 분류(EfficientNet-B0, CPU) · 대표색
   ├─ core/recommender.py   추천 규칙·점수
   ├─ core/shopping.py      구매 판정
   ├─ core/weather.py ──────► Open-Meteo API
   ├─ core/cloud_auth.py ───► Supabase Auth (이메일 로그인)
   ├─ core/cloud_store.py ──► Supabase PostgreSQL (RLS)
   └─ core/media.py ────────► Supabase Storage (비공개 버킷 wardrobe)
                                   │ 10분짜리 서명 주소
[가상 착용 버튼] ──새 창/앱 안 창──► [Hugging Face ZeroGPU Space] tryon_space/app.py (CatVTON)

[GitHub Pages] web/ ── 설치 없는 웹 체험판 (서버 없음)
```

## 3. 코드 구조

| 경로 | 역할 |
|---|---|
| `app.py` | 화면 전체, 로그인 게이트, 저장소 선택(로컬/클라우드) |
| `core/config.py` | 설정 읽기(환경 변수 → Streamlit Secrets), 서비스 키 거부, Supabase 주소 정리 |
| `core/cloud_auth.py` | 가입·로그인·세션·로그아웃, 오류 문구 변환 |
| `core/db.py` / `core/cloud_store.py` | 같은 함수 이름의 두 저장소: SQLite / Supabase. 화면 코드는 `store.함수()`만 부른다 |
| `core/media.py` | 사진 저장: `LocalMedia`(폴더) / `SupabaseMedia`(Storage, 서명 주소) |
| `core/recommender.py`, `core/taxonomy.py`, `data/clothing_taxonomy.csv` | 추천 규칙, 42종 규칙표, 겹쳐 입기·금지 조합, 계절 계산 |
| `core/shopping.py` | 사기 전에 맞춰보기 |
| `core/vision.py` | 분류 모델 불러오기·분류, 대표색 |
| `core/weather.py` | Open-Meteo 호출, 귀가 전 최저 기온 |
| `core/colors.py`, `core/care.py`, `core/catalog.py` | 색 이름·조화, 세탁 가이드, 스타터 옷장 |
| `core/tryon.py` | 가상 착용 옷 고르기, Space 주소 만들기 |
| `supabase/schema.sql` | 표·접근 정책·사진 버킷 (여러 번 실행해도 안전) |
| `tryon_space/` | Hugging Face Space에 올리는 가상 착용 앱 |
| `scripts/` | 데이터 받기·학습(`open_datasets.py`, `prepare_dataset.py`, `train_classifier.py`), 웹 체험판 만들기(`build_web.py`), Supabase 점검·이전 |
| `web/` | 웹 체험판 (`src/closet.js` 추천 규칙, `src/closetnet.js` 분류 모델 계산) |
| `tests/` | 자동 시험 (`docs/test-plan.md`) |

## 4. 데이터베이스 (Supabase PostgreSQL)

| 표 | 주요 열 | 제약 |
|---|---|---|
| `profiles` | id(=로그인 사용자), name, gender, cold_sensitivity, heat_sensitivity | id → auth.users, 계정당 1행 |
| `wardrobe_items` | user_id, name, category, subcategory, color, warmth, rain_ok, material, image_path, ai_category/ai_subcategory/ai_confidence, seasons, active, deleted | 삭제는 deleted=1 (소프트 삭제) |
| `recommendation_sessions` | user_id, purpose, temperature, apparent_temperature, humidity, precipitation, rain, wind_speed, weather_source, started_at, completed_at | 추천 1회 = 1세션 |
| `recommendations` | user_id, session_id, rank, score, pieces_json, reasons_json, components_json | 세션당 TOP 3 |
| `saved_outfits` | user_id, recommendation_id, saved_at | unique(user_id, recommendation_id) |
| `feedback` | user_id, recommendation_id, rating(like/dislike), worn, thermal_feedback(−2~2), satisfaction(1~5) | unique(user_id, recommendation_id) |
| `purchase_candidates` | user_id, name, category, subcategory, color, warmth, rain_ok, image_path, link, deleted | – |

- 모든 표의 `user_id`는 기본값 `auth.uid()`, auth.users 삭제 시 함께 삭제(cascade).
- 시각은 한국 시간 ISO 문자열(text)로 저장해 로컬 SQLite 형식과 맞춘다.
- 사진 참조: 로컬은 파일 경로, 클라우드는 `sb:<사용자id>/<파일명>.jpg`.

## 5. 인증·인가·보안

| 항목 | 설계 |
|---|---|
| 로그인 | Supabase Auth 이메일+비밀번호. 브라우저 세션마다 클라이언트 1개(`st.session_state`)를 두어 사용자끼리 섞이지 않게 함. 만료 토큰은 자동 갱신 |
| 표 접근 | 모든 표 RLS 켬. 정책: `user_id = auth.uid()`. 하위 표(추천·저장·피드백)는 부모 행도 본인 것인지 추가 확인. 비로그인(anon) 역할은 표 권한 없음 |
| 사진 | 비공개 버킷 `wardrobe`, 파일당 5MB, jpg/png/webp. 첫 폴더가 본인 id인 경로만 읽기·쓰기·삭제 |
| 키 | 앱에는 공개 키(publishable/anon)만. service_role/secret 키가 들어오면 실행 거부 |
| 비밀 설정 | Streamlit Secrets / Hugging Face Space Variables에만 둔다. 저장소에는 `.streamlit/secrets.toml.example`만 있고 실제 파일은 `.gitignore` |
| 가상 착용 | 옷 사진은 10분짜리 서명 주소로만 전달. Space는 허용 호스트(`ALLOWED_GARMENT_HOSTS`)의 https 주소만 받음. 사람 사진은 AI 옷장에 저장하지 않고, Space 임시 파일은 1시간이 지나면 정리 |
| 업로드 | Streamlit 업로드 최대 20MB, 저장 전 긴 변 1280px JPEG로 줄임 (사진 방향 보정 포함) |

## 6. 외부 API

| API | 호출 | 쓰는 값 |
|---|---|---|
| Open-Meteo `GET https://api.open-meteo.com/v1/forecast` | 위도·경도, `current`, `hourly`, `forecast_days=2`, `timezone=auto` | 기온, 체감온도, 습도, 강수, 비, 풍속 / 시간별 기온·체감온도 |
| Supabase Auth | `sign_up`, `sign_in_with_password`, `get_session`, `sign_out` | 로그인 사용자 |
| Supabase PostgREST | 표 조회·추가·수정·upsert | 4절 표 |
| Supabase Storage | upload, download, create_signed_url, remove | 옷 사진 |
| Hugging Face Space | 주소 쿼리 `?garment=…&kind=upper&garment2=…&kind2=lower` | Space가 옷 사진을 미리 채움 |

## 7. AI 구성

### 옷 분류 모델 (직접 학습)
- EfficientNet-B0(ImageNet 사전학습) + Dropout 0.3 + Linear 1280→42. 약 406만 파라미터.
- 학습: 마지막 2개 블록과 분류층만 재학습, AdamW, 클래스 가중치 1/√장수, label smoothing 0.1, 좌우 반전 증강, 15 에폭.
- 데이터 35,108장 = 학습 29,981 + 검증 5,127. 검증 정확도 83.2%, 클래스 평균 80.7%.
- 휴대폰 사진 시험 192장 (학습·검증에 안 씀, `data/grigorev_relabel.csv`의 `split=test`): 61.5%, 상위 3개 83.9%, 대분류 91.7%.
- 추론: 224×224, ImageNet 정규화, softmax 최댓값 → 세부분류 + 확신도. 대분류는 규칙표로 정한다.
- 파일: `models/clothing_classifier.pt`. 웹 체험판은 fp16 가중치(배치정규화를 conv에 합침)를 순수 JS로 계산.

### 가상 착용 (외부 공개 모델, 학습 안 함)
- CatVTON (Stable Diffusion 1.5 인페인팅 기반, CC BY-NC-SA 4.0).
- 처리: 옷 사진 받기 → DensePose·SCHP로 입힐 영역 마스크 → 사람 없음 검사 → CatVTON 생성 → 안전 필터 → 결과.
- 기본 768×1024, 단계 20/30/50. 2벌이면 상의→하의 순서로 두 번.
- ZeroGPU: `@spaces.GPU(duration=…)`로 요청 때만 GPU 사용. 예약 시간은 단계 수 × `TRYON_SEC_PER_STEP`(기본 1.0초) + 20초, 최대 120초.

## 8. 설정값

| 위치 | 이름 | 설명 |
|---|---|---|
| Streamlit Secrets | `SUPABASE_URL` | 프로젝트 주소 (대시보드 주소를 넣어도 자동 변환) |
| 〃 | `SUPABASE_KEY` | 공개 키 |
| 〃 | `TRYON_SPACE_URL` | (선택) 가상 착용 Space 페이지 주소 |
| 〃 | `WEB_TRIAL_URL` | (선택) 웹 체험판 주소 |
| Space Variables | `ALLOWED_GARMENT_HOSTS`, `TRYON_SEC_PER_STEP`, `TRYON_SIZE` | 허용 호스트, GPU 예약 계산값, 처리 해상도 |

`SUPABASE_URL`·`SUPABASE_KEY`가 없으면 로컬 모드(로그인 없음, SQLite)로 동작한다.

## 9. 배포

| 대상 | 방식 |
|---|---|
| 앱 | Streamlit Community Cloud, GitHub `main` / `app.py`, Python 3.12. `main`에 push하면 자동 재배포. 리눅스에서는 CPU 전용 PyTorch 휠을 직접 받는다 (`requirements.txt`) |
| DB·로그인·사진 | Supabase 프로젝트에 `supabase/schema.sql` 실행, 이메일 인증 끔 |
| 가상 착용 | Hugging Face ZeroGPU Space에 `tryon_space/` 파일 3개 업로드 |
| 웹 체험판 | GitHub Actions `.github/workflows/pages.yml` → GitHub Pages (`web/` 변경 시 자동, 처음은 수동 실행) |

무료 한도·잠들기·사용량 제한은 `DEPLOY.md` 5절.

## 10. 알려진 기술 과제

- 화면을 다시 그릴 때마다 Supabase를 여러 번 호출해, 실제 Supabase 연결 시 화면 전환이 수 초 걸린다 (`docs/test-plan.md` 측정값, `TASKS.md` T-05·T-06).
- 앱 안 창(`*.hf.space`)에서는 Hugging Face 방문자 확인 정보가 전달되지 않아 ZeroGPU 사용량이 방문자 각자 기준인지 확인되지 않았다. 앱은 새 창을 권장한다.
