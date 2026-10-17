"""공개 배포용 저장소: Supabase(PostgreSQL + 행 단위 보안)에 core/db.py 와 같은 함수 이름으로 읽고 쓴다.

- 로그인한 사용자의 토큰으로만 요청한다. 다른 사람 행은 DB의 RLS 정책이 막고(supabase/schema.sql),
  여기서도 user_id 를 한 번 더 확인한다.
- 테이블 구조와 값의 형태(0/1 정수, JSON 문자열, ISO 시각 문자열)는 SQLite 판과 같게 두어
  앱·추천 코드가 저장소 종류를 신경 쓰지 않게 한다.
- 조인은 사용자 한 명의 적은 데이터라 파이썬에서 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from core.catalog import starter_for_gender

KST = timezone(timedelta(hours=9))


def _now():
    return datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds")


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

    def _insert(self, table, row):
        data = self._t(table).insert(row).execute().data
        return data[0]["id"]

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
    def _item_row(self, name, category, subcategory, color, warmth, rain_ok, material="", image_path="",
                  notes="", ai_category=None, ai_subcategory=None, ai_confidence=None):
        return {
            "user_id": self.uid, "name": name, "category": category, "subcategory": subcategory, "color": color,
            "warmth": int(warmth), "rain_ok": int(bool(rain_ok)), "material": material, "image_path": image_path,
            "notes": notes, "ai_category": ai_category, "ai_subcategory": ai_subcategory,
            "ai_confidence": ai_confidence, "created_at": _now(),
        }

    def create_wardrobe_item(self, user_id, name, category, subcategory, color, warmth, rain_ok,
                             material="", image_path="", notes="", ai_category=None, ai_subcategory=None,
                             ai_confidence=None):
        self._own(user_id)
        return self._insert("wardrobe_items", self._item_row(
            name, category, subcategory, color, warmth, rain_ok, material, image_path, notes,
            ai_category, ai_subcategory, ai_confidence))

    def list_wardrobe_items(self, user_id, active_only=True):
        self._own(user_id)
        q = self._t("wardrobe_items").select("*").eq("user_id", self.uid).eq("deleted", 0)
        if active_only:
            q = q.eq("active", 1)
        return self._rows(q.order("id", desc=True))

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
        return self._rows(self._t("wardrobe_items").select("*").eq("user_id", self.uid)
                          .eq("active", 0).eq("deleted", 0).order("id", desc=True))

    def soft_delete_item(self, item_id, user_id):
        self._own(user_id)
        self._set_item(item_id, {"deleted": 1, "active": 0})

    def add_starter_wardrobe(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return 0
        existing = {x["name"] for x in self.list_wardrobe_items(user_id, active_only=False)}
        rows = [self._item_row(r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6],
                               notes="프로필 성별 기준 자동 추가된 캐주얼 스타터 옷장")
                for r in starter_for_gender(user["gender"]) if r[0] not in existing]
        if rows:
            self._t("wardrobe_items").insert(rows).execute()
        return len(rows)

    # ------------------------------------------------------------ 구매 후보
    def create_candidate(self, user_id, name, category, subcategory, color, warmth, rain_ok, image_path="",
                         link="", ai_subcategory=None, ai_confidence=None):
        self._own(user_id)
        return self._insert("purchase_candidates", {
            "user_id": self.uid, "name": name, "category": category, "subcategory": subcategory, "color": color,
            "warmth": int(warmth), "rain_ok": int(bool(rain_ok)), "image_path": image_path, "link": link,
            "ai_subcategory": ai_subcategory, "ai_confidence": ai_confidence, "created_at": _now(),
        })

    def list_candidates(self, user_id):
        self._own(user_id)
        return self._rows(self._t("purchase_candidates").select("*").eq("user_id", self.uid)
                          .eq("deleted", 0).order("id", desc=True))

    def delete_candidate(self, candidate_id, user_id):
        self._own(user_id)
        self._t("purchase_candidates").update({"deleted": 1}).eq("id", candidate_id).eq("user_id", self.uid).execute()

    def buy_candidate(self, candidate_id, user_id):
        self._own(user_id)
        rows = self._rows(self._t("purchase_candidates").select("*").eq("id", candidate_id)
                          .eq("user_id", self.uid).eq("deleted", 0))
        if not rows:
            return None
        r = rows[0]
        item_id = self.create_wardrobe_item(user_id, r["name"], r["category"], r["subcategory"], r["color"],
                                            r["warmth"], r["rain_ok"], image_path=r["image_path"] or "",
                                            notes=r["link"] or "", ai_subcategory=r["ai_subcategory"],
                                            ai_confidence=r["ai_confidence"])
        self.delete_candidate(candidate_id, user_id)
        return item_id

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
            "user_id": self.uid, "session_id": session_id, "rank": rank, "score": result["score"],
            "pieces_json": json.dumps(result["pieces"], ensure_ascii=False),
            "reasons_json": json.dumps(result["reasons"], ensure_ascii=False),
            "components_json": json.dumps(result["components"], ensure_ascii=False),
            "created_at": _now(),
        })

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

    def _sessions(self):
        return {s["id"]: s for s in self._rows(self._t("recommendation_sessions").select("*").eq("user_id", self.uid))}

    def _recommendations(self, ids=None):
        q = self._t("recommendations").select("*").eq("user_id", self.uid)
        if ids is not None:
            ids = sorted({int(i) for i in ids})
            if not ids:
                return {}
            q = q.in_("id", ids)
        return {r["id"]: r for r in self._rows(q)}

    def disliked_item_sets(self, user_id):
        self._own(user_id)
        fb = self._rows(self._t("feedback").select("recommendation_id").eq("user_id", self.uid).eq("rating", "dislike"))
        recs = self._recommendations([f["recommendation_id"] for f in fb])
        sessions = self._sessions()
        return {frozenset(int(p["id"]) for p in json.loads(r["pieces_json"]))
                for r in recs.values() if r["session_id"] in sessions}

    def count_recommendation_sessions(self, user_id):
        self._own(user_id)
        return len(self._sessions())

    def top3_acceptance_rate(self, user_id):
        self._own(user_id)
        sessions = self._sessions()
        if not sessions:
            return None
        saved = self._rows(self._t("saved_outfits").select("recommendation_id").eq("user_id", self.uid))
        recs = self._recommendations([s["recommendation_id"] for s in saved])
        accepted = {r["session_id"] for r in recs.values() if r["session_id"] in sessions}
        return len(accepted) / len(sessions)

    def list_saved_outfits(self, user_id):
        self._own(user_id)
        saved = self._rows(self._t("saved_outfits").select("*").eq("user_id", self.uid).order("id", desc=True))
        recs = self._recommendations([s["recommendation_id"] for s in saved])
        sessions = self._sessions()
        out = []
        for s in saved:
            r = recs.get(s["recommendation_id"])
            rs = sessions.get(r["session_id"]) if r else None
            if not r or not rs:
                continue
            pieces = json.loads(r["pieces_json"])
            out.append({"saved_at": s["saved_at"], "rank": r["rank"], "score": r["score"],
                        "pieces_json": r["pieces_json"], "purpose": rs["purpose"],
                        "temperature": rs["temperature"], "humidity": rs["humidity"],
                        "outfit_text": " + ".join(p["name"] for p in pieces)})
        return out

    def user_kpi_stats(self, user_id):
        return {"completed_sessions": self.count_recommendation_sessions(user_id),
                "top3_acceptance_rate": self.top3_acceptance_rate(user_id)}

    def export_user_logs(self, user_id):
        """SQLite 판과 같은 열·순서 (세션 최신순, 순위순, 추천이 없는 세션도 한 줄)."""
        self._own(user_id)
        sessions = sorted(self._sessions().values(), key=lambda s: -s["id"])
        recs_by_session = {}
        for r in self._recommendations().values():
            recs_by_session.setdefault(r["session_id"], []).append(r)
        fb = {f["recommendation_id"]: f for f in self._rows(self._t("feedback").select("*").eq("user_id", self.uid))}
        data = []
        for rs in sessions:
            base = {k: rs[k] for k in ("started_at", "completed_at", "purpose", "temperature",
                                        "apparent_temperature", "humidity", "precipitation", "rain",
                                        "wind_speed", "weather_source")}
            recs = sorted(recs_by_session.get(rs["id"], []), key=lambda r: r["rank"])
            for r in recs or [None]:
                f = fb.get(r["id"]) if r else None
                d = {"session_id": rs["id"], **base,
                     "recommendation_id": r["id"] if r else None, "rank": r["rank"] if r else None,
                     "score": r["score"] if r else None,
                     "rating": f["rating"] if f else None, "worn": f["worn"] if f else None,
                     "thermal_feedback": f["thermal_feedback"] if f else None,
                     "satisfaction": f["satisfaction"] if f else None}
                if r:
                    d["outfit"] = " + ".join(p["name"] for p in json.loads(r["pieces_json"]))
                data.append(d)
        return pd.DataFrame(data)
