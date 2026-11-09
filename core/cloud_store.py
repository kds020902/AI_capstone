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
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.catalog import starter_for_gender
from core.rows import candidate_row, wardrobe_row

KST = timezone(timedelta(hours=9))
PAGE = 1000          # 한 번에 받을 줄 수 (Supabase 기본 반환 한도). 서버 한도가 더 작아도 전체 줄 수만큼 이어 받는다
LOG_COLS = ("session_id", "started_at", "completed_at", "purpose", "temperature", "apparent_temperature", "humidity",
            "precipitation", "rain", "wind_speed", "weather_source", "recommendation_id", "rank", "score",
            "rating", "worn", "thermal_feedback", "satisfaction")    # core/db.py export 와 같은 순서 (+ 있으면 outfit)
# SQLite 에서 REAL 인 열. JSON(jsonb)을 거치면 22.0 이 22 로 와서 CSV 가 "22" 가 되므로 실수로 되돌린다 (SQLite 판은 "22.0")
LOG_FLOAT_COLS = ("temperature", "apparent_temperature", "humidity", "precipitation", "wind_speed", "score")


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

    def _all(self, make_query, desc=False):
        """여러 줄 조회를 끝까지 받는다 (키셋). make_query() 는 select(..., count="exact") 와 조건까지 정한 새 쿼리.
        id(고유하고 늘어나기만 함) 순서로 받고, 다음 쪽은 받은 마지막 id 다음부터라서 받는 도중 행이 추가·삭제돼도
        이미 받은 행이 다시 오거나 남은 행을 건너뛰지 않는다 (offset 방식은 밀리거나 당겨지고, 범위를 넘으면 416).
        끝은 쪽마다 새로 받은 '남은 줄 수'(커서 뒤 count)로 판단한다: 첫 쪽의 전체 개수로 끝내면 받는 도중 커서 뒤에
        새로 들어온 행 때문에 원래 있던 마지막 행을 놓친다. 서버 반환 한도가 PAGE 보다 작아도 남은 줄이 있으면 이어 받는다."""
        out = []
        while True:
            q = make_query()
            if out:
                q = q.lt("id", out[-1]["id"]) if desc else q.gt("id", out[-1]["id"])
            res = q.order("id", desc=desc).limit(PAGE).execute()
            rows = list(res.data or [])
            out += rows
            if not rows or (len(rows) >= res.count if res.count is not None else len(rows) < PAGE):
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

    def _save_by_request(self, table, rows, conflicts=None):
        """등록 요청 id 규칙 (SQLite 판과 같음) — 지금은 구매 후보 한 줄 저장에 쓴다 (옷 여러 벌은 DB 함수 한 번으로).
        id 가 없는 행은 늘 새로, 있는 행은 같은 id 행이 없으면 넣고, 있으면 아직 등록 상태 그대로일 때만(지우지 않음)
        지금 값으로 고친다. 그 밖의 행은 그대로 두고 요청 id 를 conflicts 에 넣는다. 한 줄이면 실제로 바뀌는 쓰기는
        넣기(on conflict do nothing) 또는 조건부 고치기 중 하나뿐이다. 요청 id 가 없는 행에도 이번 저장에서만 쓰는 id 를
        붙여, 돌아온 행을 응답 순서가 아니라 id 로 맞춘다."""
        if not rows:
            return []
        rows = [r if r["request_id"] else {**r, "request_id": str(uuid.uuid4())} for r in rows]
        last = {r["request_id"]: i for i, r in enumerate(rows)}        # 묶음 안 반복은 나중 값 하나만
        send = [r for i, r in enumerate(rows) if last[r["request_id"]] == i]
        added = {d["request_id"] for d in self._t(table).upsert(send, on_conflict="user_id,request_id",
                                                                 ignore_duplicates=True).execute().data}
        for r in send:
            if r["request_id"] in added:
                continue
            q = (self._t(table).update({k: v for k, v in r.items() if k not in ("user_id", "request_id")})
                 .eq("user_id", self.uid).eq("request_id", r["request_id"]).eq("deleted", 0))
            if table == "wardrobe_items":
                q = q.is_("edited_at", "null")
            if not q.execute().data and conflicts is not None:
                conflicts.append(r["request_id"])
        keys = list(last)
        ids = {d["request_id"]: d["id"] for d in self._all(lambda: self._t(table).select("id,request_id", count="exact")
                                                           .eq("user_id", self.uid).in_("request_id", keys))}
        return [ids[r["request_id"]] for r in rows]

    def create_wardrobe_items(self, user_id, items, conflicts=None):
        """여러 벌을 DB 함수 하나(save_wardrobe_items, 한 거래)로 저장 → id 목록(넣은 순서). 요청 id 규칙·충돌은
        SQLite 판과 같고, 중간에 실패하면 아무것도 안 바뀐다. 요청 id 가 없는 행에는 이번 저장에서만 쓰는 id 를 붙인다."""
        self._own(user_id)
        rows = [wardrobe_row(self.uid, x, _now()) for x in items]
        if not rows:
            return []
        rows = [r if r["request_id"] else {**r, "request_id": str(uuid.uuid4())} for r in rows]
        out = self._rpc("save_wardrobe_items", {"p_rows": [{k: v for k, v in r.items() if k != "user_id"} for r in rows]})
        ids = {str(o["req"]): o["item_id"] for o in out}
        if conflicts is not None:
            conflicts += [str(o["req"]) for o in out if o["kept_as_is"]]
        return [ids[r["request_id"]] for r in rows]

    def update_wardrobe_item(self, item_id, user_id, name, category, subcategory, color, warmth, rain_ok,
                             material="", notes=""):
        self._own(user_id)
        self._set_item(item_id, {"name": name, "category": category, "subcategory": subcategory, "color": color,
                                 "warmth": int(warmth), "rain_ok": int(bool(rain_ok)), "material": material,
                                 "notes": notes, "edited_at": _now()}, only_kept=True)

    def list_wardrobe_items(self, user_id, active_only=True):
        self._own(user_id)

        def query():
            q = self._t("wardrobe_items").select("*", count="exact").eq("user_id", self.uid).eq("deleted", 0)
            return q.eq("active", 1) if active_only else q
        return self._all(query, desc=True)

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
                         .eq("active", 0).eq("deleted", 0), desc=True)

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
                         link="", ai_subcategory=None, ai_confidence=None, request_id=None, conflicts=None):
        self._own(user_id)
        return self._save_by_request("purchase_candidates", [candidate_row(
            self.uid, name, category, subcategory, color, warmth, rain_ok, image_path, link, ai_subcategory,
            ai_confidence, _now(), request_id)], conflicts)[0]

    def list_candidates(self, user_id):
        self._own(user_id)
        return self._all(lambda: self._t("purchase_candidates").select("*", count="exact").eq("user_id", self.uid)
                         .eq("deleted", 0), desc=True)

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
                         .eq("user_id", self.uid))
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
                         .eq("user_id", self.uid), desc=True)
        return [{**{k: r[k] for k in ("saved_at", "rank", "score", "pieces_json", "purpose", "temperature", "humidity")},
                 "outfit_text": " + ".join(p["name"] for p in json.loads(r["pieces_json"]))} for r in rows]

    def user_kpi_stats(self, user_id):
        self._own(user_id)
        k = self._kpi()
        return {"completed_sessions": k["sessions"],
                "top3_acceptance_rate": k["accepted_sessions"] / k["sessions"] if k["sessions"] else None}

    def export_user_logs(self, user_id):
        """SQLite 판과 같은 열·순서 (세션 최신순, 순위순, 추천이 없는 세션도 한 줄). DB 함수 한 번이 JSON 하나로 돌려줘서
        한 시점의 전체 기록이고(나눠 받다 섞이지 않음) 반환 줄 한도도 받지 않는다. 옷 이름 줄(outfit)은 DB가 만든다."""
        self._own(user_id)
        data = []
        for r in self._rpc("export_user_logs", {}):
            d = {k: (float(r[k]) if k in LOG_FLOAT_COLS and r[k] is not None else r[k]) for k in LOG_COLS}
            if r.get("outfit") is not None:
                d["outfit"] = r["outfit"]
            data.append(d)
        return pd.DataFrame(data)
