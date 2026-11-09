"""공개 배포용 저장소: Supabase(PostgreSQL + 행 단위 보안)에 core/db.py 와 같은 함수 이름으로 읽고 쓴다.

- 로그인한 사용자의 토큰으로만 요청한다. 다른 사람 행은 DB의 RLS 정책이 막고(supabase/schema.sql),
  여기서도 user_id 를 한 번 더 확인한다.
- 테이블 구조와 값의 형태(0/1 정수, JSON 문자열, ISO 시각 문자열)는 SQLite 판과 같게 두어
  앱·추천 코드가 저장소 종류를 신경 쓰지 않게 한다.
- 여러 표를 잇는 조회·집계는 DB 뷰(supabase/schema.sql)가 하고, 여러 줄 조회는 정렬을 정해 한도씩 끝까지 받는다
  (Supabase 는 한 번에 최대 1,000줄만 돌려준다).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.catalog import starter_for_gender
from core.rows import candidate_row, wardrobe_row

KST = timezone(timedelta(hours=9))
PAGE = 1000          # 한 번에 받을 줄 수 (Supabase 기본 반환 한도). 서버 한도가 더 작아도 받은 만큼씩 넘어가 결과는 같다
LOG_COLS = ("session_id", "started_at", "completed_at", "purpose", "temperature", "apparent_temperature", "humidity",
            "precipitation", "rain", "wind_speed", "weather_source", "recommendation_id", "rank", "score",
            "pieces_json", "rating", "worn", "thermal_feedback", "satisfaction")    # core/db.py export 와 같은 순서


def _now():
    return datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds")


def _rec_json(result):
    """추천 결과 하나 → recommendations 표의 값 (JSON 문자열로)."""
    return {"score": result["score"],
            "pieces_json": json.dumps(result["pieces"], ensure_ascii=False),
            "reasons_json": json.dumps(result["reasons"], ensure_ascii=False),
            "components_json": json.dumps(result["components"], ensure_ascii=False)}


class CloudStore:
    def __init__(self, client, user_id):
        self.client = client
        self.uid = str(user_id)

    # ------------------------------------------------------------ 공통
    def _own(self, user_id):
        if str(user_id) != self.uid:
            raise PermissionError("다른 사용자의 데이터에는 접근할 수 없어요.")

    def _t(self, name):
        return self.client.table(name)

    def _rows(self, query):
        return list(query.execute().data or [])

    def _all(self, make_query):
        """여러 줄 조회를 끝까지 받는다. make_query() 는 select(..., count="exact") 와 정렬까지 정한 새 쿼리를 만든다
        (같은 쿼리에 range 를 두 번 걸 수 없어서 쪽마다 새로 만든다). 전체 줄 수(count)만큼 받을 때까지 이어 받는다."""
        out = []
        while True:
            res = make_query().range(len(out), len(out) + PAGE - 1).execute()
            rows = list(res.data or [])
            out += rows
            done = len(out) >= res.count if res.count is not None else len(rows) < PAGE
            if not rows or done:
                return out

    def _insert(self, table, row):
        data = self._t(table).insert(row).execute().data
        return data[0]["id"]

    def _rpc(self, fn, params):
        """DB 함수(supabase/schema.sql) 한 번 = 거래 하나. 함수 안에서도 부른 사용자의 RLS 가 그대로 적용된다."""
        return list(self.client.rpc(fn, params).execute().data or [])

    def init_db(self):
        """표는 supabase/schema.sql 로 미리 만든다."""

    # ------------------------------------------------------------ 사용자(프로필)
    def list_users(self):
        u = self.get_user(self.uid)
        return [u] if u else []

    def get_user(self, user_id):
        if str(user_id) != self.uid:
            return None
        rows = self._rows(self._t("profiles").select("*").eq("id", self.uid))
        return rows[0] if rows else None

    def create_user(self, name, gender, cold_sensitivity, heat_sensitivity):
        self._t("profiles").insert({
            "id": self.uid, "name": name, "gender": gender, "cold_sensitivity": cold_sensitivity,
            "heat_sensitivity": heat_sensitivity, "baseline_decision_seconds": 0, "created_at": _now(),
        }).execute()
        return self.uid

    def update_user(self, user_id, gender, cold_sensitivity, heat_sensitivity):
        self._own(user_id)
        self._t("profiles").update({"gender": gender, "cold_sensitivity": cold_sensitivity,
                                    "heat_sensitivity": heat_sensitivity}).eq("id", self.uid).execute()

    # ------------------------------------------------------------ 옷장
    def create_wardrobe_item(self, user_id, name, category, subcategory, color, warmth, rain_ok,
                             material="", image_path="", notes="", ai_category=None, ai_subcategory=None,
                             ai_confidence=None):
        self._own(user_id)
        return self._insert("wardrobe_items", wardrobe_row(self.uid, {
            "name": name, "category": category, "subcategory": subcategory, "color": color, "warmth": warmth,
            "rain_ok": rain_ok, "material": material, "image_path": image_path, "notes": notes,
            "ai_category": ai_category, "ai_subcategory": ai_subcategory, "ai_confidence": ai_confidence}, _now()))

    def _insert_once(self, table, rows):
        """요청 id 가 있는 행들을 넣는다. 같은 요청 id 로 이미 들어간 행은 건너뛰고(DB 고유 색인), 모든 행의 id 를
        넣은 순서대로 돌려준다 → 저장 응답을 못 받고 다시 보내도 한 번만 들어간다."""
        self._t(table).upsert(rows, on_conflict="user_id,request_id", ignore_duplicates=True).execute()
        keys = [r["request_id"] for r in rows]
        got = {r["request_id"]: r["id"] for r in self._all(lambda: self._t(table).select("id,request_id", count="exact")
                                                             .eq("user_id", self.uid).in_("request_id", keys).order("id"))}
        return [got[k] for k in keys]

    def create_wardrobe_items(self, user_id, items):
        """request_id 가 모두 있으면 같은 요청은 한 번만 넣는다 (SQLite 판과 같음)."""
        self._own(user_id)
        rows = [wardrobe_row(self.uid, x, _now()) for x in items]
        if not rows:
            return []
        if all(r["request_id"] for r in rows):
            return self._insert_once("wardrobe_items", rows)
        return [r["id"] for r in self._t("wardrobe_items").insert(rows).execute().data]

    def update_wardrobe_item(self, item_id, user_id, name, category, subcategory, color, warmth, rain_ok,
                             material="", notes=""):
        self._own(user_id)
        self._set_item(item_id, {"name": name, "category": category, "subcategory": subcategory, "color": color,
                                 "warmth": int(warmth), "rain_ok": int(bool(rain_ok)), "material": material,
                                 "notes": notes}, only_kept=True)

    def list_wardrobe_items(self, user_id, active_only=True):
        self._own(user_id)

        def query():
            q = self._t("wardrobe_items").select("*", count="exact").eq("user_id", self.uid).eq("deleted", 0)
            return (q.eq("active", 1) if active_only else q).order("id", desc=True)
        return self._all(query)

    def count_wardrobe_items(self, user_id):
        return len(self.list_wardrobe_items(user_id, active_only=True))

    def _set_item(self, item_id, values, only_kept=False):
        q = self._t("wardrobe_items").update(values).eq("id", item_id).eq("user_id", self.uid)
        if only_kept:
            q = q.eq("deleted", 0)
        q.execute()

    def deactivate_item(self, item_id, user_id):
        self._own(user_id)
        self._set_item(item_id, {"active": 0})

    def reactivate_item(self, item_id, user_id):
        self._own(user_id)
        self._set_item(item_id, {"active": 1}, only_kept=True)

    def update_item_seasons(self, item_id, user_id, seasons):
        self._own(user_id)
        self._set_item(item_id, {"seasons": "|".join(seasons) if seasons else None})

    def set_items_active(self, item_ids, user_id, active):
        self._own(user_id)
        ids = [int(i) for i in item_ids]
        if ids:
            (self._t("wardrobe_items").update({"active": int(bool(active))})
             .in_("id", ids).eq("user_id", self.uid).eq("deleted", 0).execute())

    def list_inactive_items(self, user_id):
        self._own(user_id)
        return self._all(lambda: self._t("wardrobe_items").select("*", count="exact").eq("user_id", self.uid)
                         .eq("active", 0).eq("deleted", 0).order("id", desc=True))

    def soft_delete_item(self, item_id, user_id):
        self._own(user_id)
        self._set_item(item_id, {"deleted": 1, "active": 0})

    def add_starter_wardrobe(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return 0
        existing = {x["name"] for x in self.list_wardrobe_items(user_id, active_only=False)}
        now = _now()
        rows = [wardrobe_row(self.uid, {"name": r[0], "category": r[1], "subcategory": r[2], "color": r[3],
                                        "warmth": r[4], "rain_ok": r[5], "material": r[6],
                                        "notes": "프로필 성별 기준 자동 추가된 캐주얼 스타터 옷장"}, now)
                for r in starter_for_gender(user["gender"]) if r[0] not in existing]
        if rows:
            self._t("wardrobe_items").insert(rows).execute()
        return len(rows)

    # ------------------------------------------------------------ 구매 후보
    def create_candidate(self, user_id, name, category, subcategory, color, warmth, rain_ok, image_path="",
                         link="", ai_subcategory=None, ai_confidence=None, request_id=None):
        self._own(user_id)
        row = candidate_row(self.uid, name, category, subcategory, color, warmth, rain_ok, image_path, link,
                            ai_subcategory, ai_confidence, _now(), request_id)
        if request_id:
            return self._insert_once("purchase_candidates", [row])[0]
        return self._insert("purchase_candidates", row)

    def list_candidates(self, user_id):
        self._own(user_id)
        return self._all(lambda: self._t("purchase_candidates").select("*", count="exact").eq("user_id", self.uid)
                         .eq("deleted", 0).order("id", desc=True))

    def delete_candidate(self, candidate_id, user_id):
        self._own(user_id)
        self._t("purchase_candidates").update({"deleted": 1}).eq("id", candidate_id).eq("user_id", self.uid).execute()

    def buy_candidate(self, candidate_id, user_id):
        """후보 빼기와 옷장 넣기를 DB 함수 하나(한 거래)로 한다. 중간에 실패하면 둘 다 그대로 남고,
        이미 뺀 후보면 None 이라 두 번 누르거나 응답을 못 받고 다시 눌러도 옷은 한 벌만 들어간다."""
        self._own(user_id)
        rows = self._rpc("buy_candidate", {"p_candidate_id": int(candidate_id), "p_created_at": _now()})
        return rows[0]["new_item_id"] if rows else None

    # ------------------------------------------------------------ 추천 기록
    def create_recommendation_session(self, user_id, purpose, temperature, apparent_temperature,
                                      humidity, precipitation, rain, wind_speed, weather_source):
        self._own(user_id)
        return self._insert("recommendation_sessions", {
            "user_id": self.uid, "purpose": purpose, "temperature": temperature,
            "apparent_temperature": apparent_temperature, "humidity": humidity, "precipitation": precipitation,
            "rain": int(bool(rain)), "wind_speed": wind_speed, "weather_source": weather_source,
            "started_at": _now(),
        })

    def save_recommendation(self, session_id, rank, result):
        return self._insert("recommendations", {
            "user_id": self.uid, "session_id": session_id, "rank": rank, **_rec_json(result), "created_at": _now(),
        })

    def save_recommendation_session(self, user_id, request_id, purpose, temperature, apparent_temperature,
                                    humidity, precipitation, rain, wind_speed, weather_source, results):
        """추천 세션과 결과를 DB 함수 하나(한 거래)로 저장 → (세션 id, 추천 id 목록 순위순).
        같은 request_id 로 다시 부르면 새로 넣지 않고 처음 저장한 묶음을 돌려준다 (응답을 못 받고 재시도할 때)."""
        self._own(user_id)
        rows = self._rpc("save_recommendation_session", {
            "p_request_id": str(request_id), "p_purpose": purpose, "p_temperature": temperature,
            "p_apparent_temperature": apparent_temperature, "p_humidity": humidity, "p_precipitation": precipitation,
            "p_rain": int(bool(rain)), "p_wind_speed": wind_speed, "p_weather_source": weather_source,
            "p_started_at": _now(), "p_results": [_rec_json(r) for r in results],
        })
        return rows[0]["new_session_id"], list(rows[0]["new_recommendation_ids"])

    def complete_recommendation_session(self, session_id):
        (self._t("recommendation_sessions").update({"completed_at": _now()})
         .eq("id", session_id).eq("user_id", self.uid).is_("completed_at", "null").execute())

    def save_outfit(self, user_id, recommendation_id):
        self._own(user_id)
        (self._t("saved_outfits")
         .upsert({"user_id": self.uid, "recommendation_id": recommendation_id, "saved_at": _now()},
                 on_conflict="user_id,recommendation_id", ignore_duplicates=True)
         .execute())

    def upsert_feedback(self, user_id, recommendation_id, rating=None, worn=None,
                        thermal_feedback=None, satisfaction=None):
        self._own(user_id)
        old = self._rows(self._t("feedback").select("*").eq("user_id", self.uid)
                         .eq("recommendation_id", recommendation_id))
        if old:
            o = old[0]
            (self._t("feedback").update({
                "rating": rating if rating is not None else o["rating"],
                "worn": int(worn) if worn is not None else o["worn"],
                "thermal_feedback": thermal_feedback if thermal_feedback is not None else o["thermal_feedback"],
                "satisfaction": satisfaction if satisfaction is not None else o["satisfaction"],
                "updated_at": _now(),
            }).eq("user_id", self.uid).eq("recommendation_id", recommendation_id).execute())
        else:
            self._t("feedback").insert({
                "user_id": self.uid, "recommendation_id": recommendation_id, "rating": rating,
                "worn": int(worn) if worn is not None else None, "thermal_feedback": thermal_feedback,
                "satisfaction": satisfaction, "updated_at": _now(),
            }).execute()

    def _kpi(self):
        rows = self._rows(self._t("kpi_rows").select("sessions,accepted_sessions"))
        return rows[0] if rows else {"sessions": 0, "accepted_sessions": 0}

    def disliked_item_sets(self, user_id):
        self._own(user_id)
        rows = self._all(lambda: self._t("disliked_outfit_rows").select("id,pieces_json", count="exact")
                         .eq("user_id", self.uid).order("id"))
        return {frozenset(int(p["id"]) for p in json.loads(r["pieces_json"])) for r in rows}

    def count_recommendation_sessions(self, user_id):
        self._own(user_id)
        return self._kpi()["sessions"]

    def top3_acceptance_rate(self, user_id):
        self._own(user_id)
        k = self._kpi()
        return k["accepted_sessions"] / k["sessions"] if k["sessions"] else None

    def list_saved_outfits(self, user_id):
        self._own(user_id)
        rows = self._all(lambda: self._t("saved_outfit_rows")
                         .select("id,saved_at,rank,score,pieces_json,purpose,temperature,humidity", count="exact")
                         .eq("user_id", self.uid).order("id", desc=True))
        return [{**{k: r[k] for k in ("saved_at", "rank", "score", "pieces_json", "purpose", "temperature", "humidity")},
                 "outfit_text": " + ".join(p["name"] for p in json.loads(r["pieces_json"]))} for r in rows]

    def user_kpi_stats(self, user_id):
        self._own(user_id)
        k = self._kpi()
        return {"completed_sessions": k["sessions"],
                "top3_acceptance_rate": k["accepted_sessions"] / k["sessions"] if k["sessions"] else None}

    def export_user_logs(self, user_id):
        """SQLite 판과 같은 열·순서 (세션 최신순, 순위순, 추천이 없는 세션도 한 줄). 줄이 많아도 끝까지 나눠 받는다."""
        self._own(user_id)
        rows = self._all(lambda: self._t("recommendation_log_rows").select(",".join(LOG_COLS), count="exact")
                         .eq("user_id", self.uid).order("session_id", desc=True).order("rank")
                         .order("recommendation_id"))
        data = []
        for r in rows:
            d = {k: r[k] for k in LOG_COLS if k != "pieces_json"}
            if r["pieces_json"]:
                d["outfit"] = " + ".join(p["name"] for p in json.loads(r["pieces_json"]))
            data.append(d)
        return pd.DataFrame(data)
