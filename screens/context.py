"""한 번 화면을 그리는 동안 쓰는 공통 상태: 저장소·사진 저장소·설정, 그리고 그동안만 쓰는 캐시.

Streamlit은 버튼을 누를 때마다 app.py를 처음부터 다시 실행한다. app.py가 실행될 때마다 App을 새로 만들어
화면 함수에 넘기므로 캐시는 한 번 그리는 동안만 산다. (모듈 전역 변수에 두면 다른 사용자의 세션과 섞인다.)
"""
from __future__ import annotations

import streamlit as st

from core.media import PhotoCache, thumbnail_jpeg

# 옷장이나 추천에 쓰는 프로필 값(체질)을 바꾸는 저장소 함수. 부르면 지난 추천을 비우고 이번 화면의 캐시도 버린다.
# 새 함수를 만들면 여기에 넣는다 (tests/test_screens.py 가 core/db.py 에서 옷장을 고치는 함수를 찾아 확인한다).
WARDROBE_CHANGES = frozenset({
    "create_wardrobe_item", "create_wardrobe_items", "update_wardrobe_item", "deactivate_item", "reactivate_item",
    "soft_delete_item", "set_items_active", "update_item_seasons", "add_starter_wardrobe", "buy_candidate",
    "update_user",
})


def clear_recommendations():
    """지난 추천을 비운다 (안 비우면 지운·넣어 둔 옷이 든 추천이 그대로 보이고 저장될 수 있음)."""
    st.session_state.recommendations = None
    st.session_state.recommendation_session_id = None
    st.session_state.rec_meta = None


class WatchedStore:
    """저장소(core/db.py 모듈 또는 CloudStore)를 감싸서, 옷장을 바꾸는 함수가 성공하면 on_change 를 부른다."""

    def __init__(self, store, on_change):
        self._store, self._on_change = store, on_change

    def __getattr__(self, name):
        attr = getattr(self._store, name)
        if name not in WARDROBE_CHANGES:
            return attr

        def call(*args, **kwargs):
            result = attr(*args, **kwargs)
            self._on_change()
            return result
        return call


class App:
    def __init__(self, store, media, cloud=None, tryon_url=None, web_url=None):
        self.store = WatchedStore(store, self._changed)
        self.media = media
        self.cloud = cloud
        self.tryon_url = tryon_url
        self.web_url = web_url
        self._run = {}

    def _changed(self):
        clear_recommendations()
        self._run.clear()

    def current_user(self):
        uid = st.session_state.user_id
        if not uid:
            return None
        if ("user", uid) not in self._run:
            self._run[("user", uid)] = self.store.get_user(uid)
        return self._run[("user", uid)]

    def need_user(self):
        """사용자가 없으면 어디서 만드는지 안내하고 화면을 멈춘다."""
        user = self.current_user()
        if not user:
            st.info("프로필에서 내 정보를 먼저 저장해 주세요." if self.cloud else "프로필에서 사용자를 먼저 만들어 주세요.")
            st.stop()
        return user

    def wardrobe(self, user_id):
        """옷장 전체(지운 옷 제외)를 한 번만 읽는다. 추천에 쓰는 옷/넣어 둔 옷은 active로 나눈다."""
        if ("items", user_id) not in self._run:
            self._run[("items", user_id)] = self.store.list_wardrobe_items(user_id, active_only=False)
        return self._run[("items", user_id)]

    def active_items(self, user_id):
        return [x for x in self.wardrobe(user_id) if x["active"]]

    def inactive_items(self, user_id):
        return [x for x in self.wardrobe(user_id) if not x["active"]]

    def show_image(self, ref, thumb=False, **kwargs):
        """사진을 보여 준다. thumb=True 면 목록용 작은 사진 (세션에 담아 두고 다시 만들지 않는다)."""
        if thumb and ref:
            thumbs = st.session_state.setdefault("thumb_cache", PhotoCache(15_000_000))
            if ref not in thumbs:
                data = self.media.read(ref)
                if not data:
                    return False
                thumbs[ref] = thumbnail_jpeg(data)
            st.image(thumbs[ref], **kwargs)
            return True
        data = self.media.read(ref)
        if data:
            st.image(data, **kwargs)
            return True
        return False
