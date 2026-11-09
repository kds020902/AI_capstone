"""웹 체험판 배포 파일(web/index.html)이 지금 소스로 만든 것과 같은지. Node.js 없이도 돈다."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_web_index_is_rebuilt_from_current_sources(tmp_path):
    """GitHub Pages 는 저장소의 web/index.html 을 그대로 올린다. 규칙(파이썬 값)이나 web/src 를 고치고
    `python scripts/build_web.py --skip-model` 을 잊으면 여기서(그리고 CI에서) 실패한다.
    모델 파일(web/model/)이 최신인지는 이 시험이 보지 않는다 (모델은 index.html 에 들어 있지 않음)."""
    from scripts.build_web import assemble
    out = tmp_path / "index.html"
    assemble(out)
    same = out.read_bytes() == (ROOT / "web" / "index.html").read_bytes()
    assert same, "web/index.html 이 지금 소스로 만든 것과 달라요: python scripts/build_web.py --skip-model"
