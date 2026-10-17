---
title: AI Closet Try-On
emoji: 👗
colorFrom: indigo
colorTo: pink
sdk: gradio
sdk_version: 5.49.1
python_version: "3.12"
app_file: app.py
pinned: false
license: cc-by-nc-sa-4.0
short_description: AI 옷장 가상 착용(예시) - CatVTON on ZeroGPU
---

# AI 옷장 · 가상 착용(예시)

캡스톤 프로젝트 [AI 옷장](https://github.com/kds020902/AI_capstone)의 가상 착용 시험 기능이에요.
비상업·학습·시연용입니다.

- 모델: 외부 공개 사전학습 모델 [CatVTON](https://github.com/Zheng-Chong/CatVTON) (CC BY-NC-SA 4.0).
  AI 옷장에서 직접 학습한 모델이 아니에요.
- 입력: 사람 사진 1장 + 옷 사진 1~2장 (상의·원피스·아우터 → 하의 순서로 입힘). 신발은 지원하지 않아요.
- 결과는 AI가 그린 예시 이미지이며 실제 사이즈·핏은 반영되지 않아요.
- 사람 사진과 결과는 저장하지 않아요. Gradio 임시 파일은 30분마다 정리해 1시간이 넘은 것을 지워요.
- 무료 GPU(ZeroGPU)라 대기열과 방문자별 하루 사용량 제한이 있어요 (비로그인: 인터넷 주소(IP)당 약 2분,
  Hugging Face 무료 계정으로 로그인해 이 페이지에서 쓰면 계정당 약 5분, 첫 사용 24시간 뒤 다시 채워짐).
  다른 사이트 안에 띄운 창(*.hf.space)에서는 로그인이 전달되지 않아 비로그인 기준이 적용돼요.

## 설정 (Space Settings → Variables)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `ALLOWED_GARMENT_HOSTS` | `.supabase.co` | 옷 사진을 받아 올 수 있는 호스트 (쉼표로 구분, `.`으로 시작하면 하위 도메인 허용). 내 Supabase 주소(예: `abcd1234.supabase.co`)만 넣는 것을 권장 |
| `TRYON_SEC_PER_STEP` | `1.0` | GPU 예약 시간 계산값 (단계당 초). Logs 탭의 `GPU 사용 n초` 를 보고 줄이면 대기 순서가 빨라지고, 남은 사용량이 적은 방문자도 실행할 수 있어요 |
| `TRYON_SIZE` | `768x1024` | 처리 해상도 (가로x세로) |
