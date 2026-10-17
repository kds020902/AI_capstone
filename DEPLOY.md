# AI 옷장 무료 공개 배포 안내

PC나 Colab을 켜 두지 않아도 되는 무료 서비스만 씁니다. 비용이 드는 설정(유료 요금제, 유료 GPU, 크레딧)은 쓰지 않습니다.

| 역할 | 서비스 (무료) | 주소 |
|---|---|---|
| 앱 (입구 주소) | Streamlit Community Cloud | `https://<앱이름>.streamlit.app` |
| 로그인 · DB · 옷 사진 | Supabase Free | 앱이 안에서만 사용 |
| 가상 착용(예시) | Hugging Face ZeroGPU Space | 앱 안 창으로 열림 (`https://huggingface.co/spaces/<아이디>/<이름>`) |
| 설치 없는 웹 체험판 (선택) | GitHub Pages | `https://kds020902.github.io/AI_capstone/` |

사용자는 **앱 주소 하나**로 들어와 가입·로그인하고, 옷장·추천·구매 후보·KPI·가상 착용을 모두 그 안에서 씁니다.
각 사용자의 데이터와 사진은 Supabase의 행 단위 보안(RLS)으로 본인만 읽고 쓸 수 있습니다.

설정이 없으면 앱은 예전처럼 **로컬 모드**(로그인 없음, `data/ai_wardrobe.sqlite3` + `data/uploads/`)로 동작합니다.

---

## 1. Supabase (로그인 · DB · 사진)

1. <https://supabase.com> 에 가입하고 **New project** (Free). 지역은 `Northeast Asia (Seoul)` 권장. DB 비밀번호는 따로 적어 둡니다.
2. 왼쪽 **SQL Editor → New query** 에 저장소의 `supabase/schema.sql` 내용을 전부 붙여 넣고 **Run**.
   - 표 7개, 사용자별 접근 정책(RLS), 비공개 사진 버킷 `wardrobe`(파일당 5MB, jpg/png/webp)가 만들어집니다.
   - 여러 번 실행해도 안전합니다.
3. **Authentication → Sign In / Providers → Email** 에서 **Confirm email 을 끕니다.**
   - Supabase 기본 메일 발송은 프로젝트 팀원 주소로만 보내고 시간당 2통으로 제한돼 있어서, 켜 두면 다른 사람은 가입 인증 메일을 받을 수 없습니다.
   - 끄면 가입 즉시 로그인됩니다. 대신 아무 이메일 주소로나 가입할 수 있고, 비밀번호 찾기 메일도 보낼 수 없습니다(앱에도 그 기능은 없음).
   - 인증 메일이 꼭 필요하면 무료 SMTP 서비스를 따로 가입해 **Authentication → Emails → SMTP Settings** 에 연결합니다. 앱은 두 경우 모두 지원합니다(켜져 있으면 "인증 링크를 확인해 주세요" 안내).
4. **Project Settings → API Keys** 에서 두 값을 복사합니다.
   - Project URL: `https://abcd1234.supabase.co`
   - **anon public** 키(`eyJ...`) 또는 **publishable** 키(`sb_publishable_...`)
   - `service_role` / `secret` 키는 쓰지 않습니다. 모든 사용자 데이터를 열 수 있는 키라서 앱에 넣으면 앱이 멈추고 안내합니다.

### (선택) 실제 프로젝트 점검

앱에서 시험용 계정 두 개를 가입해 둔 뒤, PC에서:

```bash
set SUPABASE_URL=https://abcd1234.supabase.co      # macOS/Linux 는 export
set SUPABASE_KEY=<anon 또는 publishable 키>
python scripts/check_supabase.py --email-a test1@example.com --email-b test2@example.com
```

저장·추천·피드백·CSV·사진 올리기/서명 주소와 "B가 A의 데이터·사진을 못 읽고 못 바꾸는지"를 확인하고, 시험 데이터는 지웁니다.

### (선택) 로컬 데이터 옮기기

```bash
python scripts/migrate_to_supabase.py --list                       # 로컬 사용자 목록
python scripts/migrate_to_supabase.py --user-id 1 --email me@example.com
```

옮길 계정으로 앱에서 먼저 가입해 두세요. 비밀번호는 실행 중에 묻습니다.

---

## 2. Hugging Face ZeroGPU Space (가상 착용)

> 조건: Hugging Face 무료 계정은 **이메일 인증을 마치고 가입한 지 30일이 지나야** ZeroGPU Space를 만들 수 있습니다 (계정당 2개까지).
> 무료 계정은 일반 CPU Gradio Space는 만들 수 없고, ZeroGPU Space만 무료입니다. 조건이 안 되면 가상 착용 없이 배포해도 나머지 기능은 그대로 동작합니다 (버튼만 숨겨짐).

1. <https://huggingface.co/new-space> 에서
   - Space name: 예) `ai-closet-tryon`
   - SDK: **Gradio**, Hardware: **ZeroGPU**, Visibility: **Public**
2. 만든 Space의 **Files → Add file → Upload files** 로 저장소의 `tryon_space/` 안 파일 3개(`app.py`, `requirements.txt`, `README.md`)를 올립니다.
   `README.md` 맨 위 설정(파이썬 3.12, Gradio 5.49.1)이 그대로 적용됩니다.
3. **Settings → Variables and secrets → New variable**
   - `ALLOWED_GARMENT_HOSTS` = `abcd1234.supabase.co` (내 Supabase 주소의 호스트만)
4. 처음 시작할 때 CatVTON 코드와 모델(약 5~6GB)을 받느라 10~20분 걸릴 수 있습니다. **Logs** 탭에서 `Running on` 이 보이면 준비 완료.
5. Space 페이지에서 사람 사진 + 옷 사진으로 한 번 실행해 보고, Logs 의 `GPU 사용 n초` 를 확인합니다.
   단계당 시간이 1초보다 훨씬 짧으면 `TRYON_SEC_PER_STEP` 변수를 줄입니다 (예: `0.4`). GPU 예약 시간이 짧을수록 대기 순서가 빨라지고, 남은 사용량이 적은 방문자도 실행할 수 있습니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `ALLOWED_GARMENT_HOSTS` | `.supabase.co` | 옷 사진을 받아 올 수 있는 호스트 |
| `TRYON_SEC_PER_STEP` | `1.0` | GPU 예약 시간 계산값 (단계당 초) |
| `TRYON_SIZE` | `768x1024` | 처리 해상도 |

---

## 3. Streamlit Community Cloud (앱)

1. <https://share.streamlit.io> 에 GitHub 계정(`kds020902`)으로 로그인하고 저장소 접근을 허용합니다.
2. **Create app → Deploy a public app from GitHub**
   - Repository `kds020902/AI_capstone`, Branch `main`, Main file path `app.py`
   - App URL: 원하는 이름 (예: `ai-closet-anyang`)
3. **Advanced settings**
   - Python version: **3.12**
   - Secrets: 아래 형식으로 붙여 넣기 (`.streamlit/secrets.toml.example` 참고)

     ```toml
     SUPABASE_URL = "https://abcd1234.supabase.co"
     SUPABASE_KEY = "sb_publishable_..."
     TRYON_SPACE_URL = "https://huggingface.co/spaces/<아이디>/ai-closet-tryon"   # 선택
     WEB_TRIAL_URL = "https://kds020902.github.io/AI_capstone/"                   # 선택
     ```
4. **Deploy**. 첫 설치(PyTorch CPU 포함)에 몇 분 걸립니다.
5. Supabase **Authentication → URL Configuration → Site URL** 에 앱 주소를 넣어 둡니다 (인증 메일을 쓸 때 필요).

비밀 값은 이 Secrets 화면과 Supabase·Hugging Face 설정 화면에만 넣습니다. 저장소에는 예시 파일만 있고, `.streamlit/secrets.toml` 은 `.gitignore` 에 들어 있습니다.

---

## 4. 웹 체험판 (GitHub Pages, 선택)

1. 저장소 **Settings → Pages → Build and deployment → Source** 를 **GitHub Actions** 로 바꿉니다.
2. 저장소 **Actions** 탭 → **웹 체험판 배포 (GitHub Pages)** → **Run workflow** 를 한 번 누릅니다.
   그다음부터는 `main` 의 `web/` 이 바뀔 때마다 `.github/workflows/pages.yml` 이 자동으로 다시 배포합니다.
3. 주소: `https://kds020902.github.io/AI_capstone/` → 앱 Secrets 의 `WEB_TRIAL_URL` 에 넣으면 앱 사이드바에 링크가 생깁니다.

웹 체험판은 서버 없이 브라우저에서만 돌아갑니다 (로그인·저장 없음, 날씨 직접 입력).

---

## 5. 무료 사용 한도 (2026년 10월 문서 기준, 바뀔 수 있음)

| 서비스 | 한도 | 넘거나 쉬면 |
|---|---|---|
| Streamlit Community Cloud | 메모리 최대 약 2.7GB, CPU 최대 2코어 (이 앱은 분류 모델을 올린 상태로 약 0.4~0.5GB 사용) | **12시간 방문이 없으면 잠듦.** 아무 방문자나 "Yes, get this app back up!" 을 누르면 다시 켜짐 (켜지는 데 잠시 걸림, 저장된 데이터는 Supabase 에 있어 그대로) |
| Supabase Free | DB 500MB, 파일 1GB, 전송량 5GB/월, 월 사용자 5만 명, 무료 프로젝트 2개 | **1주일 동안 활동이 없으면 프로젝트가 일시 정지.** 소유자가 대시보드에서 다시 켜야 앱 로그인이 됨 (데이터는 유지) |
| Hugging Face ZeroGPU | 방문자 하루 GPU 사용량: 비로그인 2분(인터넷 주소(IP)당), 무료 계정 로그인 5분. 첫 사용 24시간 뒤 다시 채워짐. 대기열 있음 (아래 참고) | 다 쓰면 "오늘 쓸 수 있는 무료 GPU 사용량을 다 썼어요" 안내. 오래(약 48시간) 안 쓰면 Space가 잠들고 다음 방문 때 다시 켜짐(몇 분) |
| GitHub Pages | 공개 저장소 무료 | 잠들지 않음 |

가상 착용 사용량에서 꼭 알아 둘 점
- **"새 창(Hugging Face)에서 열기"(권장)**: Hugging Face 페이지가 방문자 확인 정보를 Space에 넘겨 주므로 **방문자 각자의 사용량**으로 실행됩니다 (비로그인은 인터넷 주소(IP)당 2분이라 학교 와이파이처럼 같은 주소를 쓰는 사람끼리 나눠 쓰고, 로그인하면 계정당 5분). 옷 사진 주소는 새 창에도 그대로 넘어갑니다(10분 동안 유효).
- **앱 안 창(`*.hf.space`)**: Gradio 는 부모 페이지가 `huggingface.co` 일 때만 방문자 확인 정보를 받아 오므로, AI 옷장 앱 안에서는 그 정보가 없습니다. 이때 사용량을 어떻게 세는지는 공식 문서에 없고, `spaces` 패키지는 이 경우 "Space app has reached its GPU limit" 을 낼 수 있게 되어 있어 **여러 방문자가 한도를 함께 쓸 수 있습니다.** 실제 배포 후 확인이 필요해서 앱은 새 창을 먼저 권합니다.
- 실행하려면 시작할 때 예약 시간(기본: 1벌·30단계 약 50초)만큼 사용량이 남아 있어야 합니다 (`spaces` 패키지 기준: "requested vs. left").
- AI 옷장 서버가 대신 GPU를 호출하지 않습니다. Space 주인 계정의 사용량을 쓰는 구조가 아닙니다.

---

## 6. 개인정보 · 사진 보관

- 옷 사진: Supabase 비공개 버킷 `wardrobe/<사용자 id>/` 에 저장 (긴 변 1280px JPEG로 줄여 저장). 본인만 읽을 수 있고, 가상 착용 때만 10분짜리 서명 주소로 Space에 넘깁니다.
- 사람 사진: AI 옷장에는 저장하지 않습니다. 가상 착용 Space가 처리 중에만 쓰고, Gradio 임시 파일은 30분마다 정리해 만든 지 1시간이 넘은 것을 지웁니다 (최대 약 1시간 반 보관).
- 옷을 "삭제"하면 목록에서 숨겨지고(기존과 같은 소프트 삭제) 추천 기록은 유지됩니다. 계정과 데이터를 완전히 지우려면 Supabase 대시보드에서 소유자가 사용자를 삭제합니다 (사진 파일은 Storage 에서 해당 폴더 삭제).

---

## 7. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 앱이 "service_role/secret 키가 들어 있어요" 로 멈춤 | Secrets 의 `SUPABASE_KEY` 를 anon public / publishable 키로 교체 |
| 가입 후 "인증 링크를 확인해 주세요" 만 나오고 메일이 안 옴 | Supabase 의 Confirm email 이 켜져 있음 → 1-3 참고 |
| 갑자기 모두 로그인이 안 됨 | Supabase 프로젝트가 일시 정지됐는지 대시보드에서 확인 후 Restore |
| 가상 착용 버튼이 안 보임 | Secrets 에 `TRYON_SPACE_URL` 이 있는지, 옷이 상의·하의·원피스·아우터인지 (신발 미지원) |
| 가상 착용 창에 "허용하지 않은 주소" | Space 변수 `ALLOWED_GARMENT_HOSTS` 가 내 Supabase 호스트와 같은지 |
| 앱 안 창에서 카메라가 안 켜짐 | 브라우저가 겹친 창의 카메라를 막을 수 있음 → "새 창에서 열기" 또는 사진 올리기 사용 |
| 앱 설치가 PyTorch 에서 실패 | Advanced settings 의 Python 이 3.11~3.13 인지 (3.12 권장) |
