"""옷 사진 저장소. 로컬은 data/uploads 파일, 공개 배포는 Supabase Storage(비공개 버킷)를 쓴다.

DB의 image_path 에는
  - 로컬: 파일 경로 (예전과 같음)
  - Supabase: "sb:<사용자 id>/<파일 이름>" 형태의 참조
를 저장한다. 앱은 read() 로 바이트를 받아 보여 주므로 저장소 종류를 몰라도 된다.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image, ImageOps

SB_PREFIX = "sb:"
BUCKET = "wardrobe"
CLOUD_MAX_SIDE = 1280      # 무료 저장 용량(1GB)을 아끼려고 긴 변을 이만큼으로 줄여 JPEG로 저장
CLOUD_JPEG_QUALITY = 88


def open_upright(src) -> Image.Image:
    """사진을 회전 정보(EXIF)대로 바로 세운 RGB 이미지로 연다.
    폰 사진은 누운 채 저장되고 회전 정보만 붙어 있는 경우가 많다. 누운 채 분류하면 정확도가 크게 떨어진다
    (검증 사진 1,000장을 90도 눕히면 83.6% → 28.3%)."""
    return ImageOps.exif_transpose(Image.open(src)).convert("RGB")


def to_cloud_jpeg(data: bytes) -> bytes:
    """폰 사진(회전 정보 포함)을 바로 세우고, 긴 변 1280px JPEG로 줄인다."""
    img = open_upright(io.BytesIO(data))
    img.thumbnail((CLOUD_MAX_SIDE, CLOUD_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=CLOUD_JPEG_QUALITY)
    return out.getvalue()


class LocalMedia:
    kind = "local"

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, user_id, prefix, data: bytes, suffix=".jpg"):
        path = self.folder / f"{prefix}{user_id}_{int(time.time() * 1000)}{suffix}"
        path.write_bytes(data)
        return str(path)

    def read(self, ref):
        if not ref or str(ref).startswith(SB_PREFIX):
            return None
        p = Path(ref)
        return p.read_bytes() if p.exists() else None

    def signed_url(self, ref, seconds=600):
        return None


class SupabaseMedia:
    kind = "supabase"

    def __init__(self, client, user_id):
        self.client = client
        self.uid = str(user_id)
        self._cache = {}

    def _bucket(self):
        return self.client.storage.from_(BUCKET)

    def save(self, user_id, prefix, data: bytes, suffix=".jpg"):
        if str(user_id) != self.uid:
            raise PermissionError("다른 사용자의 폴더에는 저장할 수 없어요.")
        jpeg = to_cloud_jpeg(data)
        path = f"{self.uid}/{prefix}{int(time.time() * 1000)}.jpg"
        self._bucket().upload(path, jpeg, {"content-type": "image/jpeg", "upsert": "false"})
        self._cache[path] = jpeg
        return SB_PREFIX + path

    def _path(self, ref):
        if not ref or not str(ref).startswith(SB_PREFIX):
            return None
        path = str(ref)[len(SB_PREFIX):]
        return path if path.split("/", 1)[0] == self.uid else None

    def read(self, ref):
        path = self._path(ref)
        if path is None:
            return None
        if path not in self._cache:
            try:
                self._cache[path] = self._bucket().download(path)
            except Exception:
                return None
        return self._cache[path]

    def signed_url(self, ref, seconds=600):
        path = self._path(ref)
        if path is None:
            return None
        try:
            res = self._bucket().create_signed_url(path, seconds)
        except Exception:
            return None
        return res.get("signedURL") or res.get("signedUrl")
