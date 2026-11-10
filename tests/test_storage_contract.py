"""저장 계약: 로컬(SQLite)과 Supabase(PostgreSQL) 저장소가 같은 열을 갖고, 넣은 값이 실제로 저장·조회되는지.

1) 표 구조 — SQLite 는 실제 init_db 실행 뒤 PRAGMA, PostgreSQL 은 schema.sql 적용 뒤 information_schema 로
   표마다 열·타입·NOT NULL·기본값을 비교한다. 일부러 다른 곳은 INTENDED 에 이유와 함께 적는다.
   core/rows.py 는 두 표(옷·구매 후보)의 입력 행 생성기이고 가짜 백엔드의 DEFAULTS 는 기본값 일부라서,
   전체 열 목록이 아니라 '실제 표에 있는 열인지 / 기본값이 같은지'만 본다.
2) 값 보존 — 쓰기 경로마다(DB 함수 포함) 기본값과 다른 값을 넣고, DB 에 저장된 행과 저장소 읽기 함수 결과에서
   그 값이 그대로인지 본다. 가짜 백엔드는 DB 함수 입력을 그대로 펼쳐 저장하지만 실제 SQL 은 열을 하나하나 적으므로,
   열 비교만 통과하고 값은 빠질 수 있다 → 실제 PostgreSQL 에서 확인한다 (AI_CLOSET_TEST_PG_DSN 필요).
실패하면 표·저장소·단계·빠진/더 있는 열·넣은 값과 저장된 값을 모두 출력한다.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from core import db
from core.cloud_store import CloudStore
from core.rows import candidate_row, wardrobe_row
from tests.fake_supabase import DEFAULTS as FAKE_DEFAULTS
from tests.test_rls_sql import DSN, ROOT, As, _PgClient

psycopg = pytest.importorskip("psycopg") if DSN else None
need_pg = pytest.mark.skipif(not DSN, reason="AI_CLOSET_TEST_PG_DSN 이 없어 PostgreSQL 저장 계약 검사를 건너뜀")

PG_TABLE = {"users": "profiles"}          # SQLite 표 이름 → Supabase 표 이름 (나머지는 같음)
TABLES = ("users", "wardrobe_items", "purchase_candidates", "recommendation_sessions", "recommendations",
          "saved_outfits", "feedback")
SQLITE_TYPES = {"INTEGER": {"integer", "bigint"}, "REAL": {"double precision"}, "TEXT": {"text"}}

# 일부러 다른 곳: (SQLite 표, 열) → 이유. 표 이름 "*" 는 모든 표. 목록에 있는데 실제로 차이가 없으면 시험이 알려 준다.
INTENDED = {
    ("users", "id"): "SQLite 는 1부터 늘어나는 번호, Supabase 는 로그인 계정 id(uuid)를 그대로 씀 (표 이름도 profiles)",
    ("*", "user_id"): "사용자 id: SQLite 정수 ↔ Supabase uuid (기본값 auth.uid(), 행 단위 보안의 기준)",
    ("*", "request_id"): "등록 요청 id: SQLite 문자열 ↔ Supabase uuid",
    ("recommendations", "user_id"): "Supabase 에만 있음: 행 단위 보안이 이 열로 주인을 확인 (SQLite 는 세션을 거쳐 확인)",
}


def _intended(table, col):
    return (table, col) in INTENDED or ("*", col) in INTENDED


def _norm_default(v):
    """기본값 표기를 맞춘다: PostgreSQL 의 'male'::text → male, SQLite 의 'male' → male, 숫자는 숫자로."""
    if v is None:
        return None
    v = str(v).split("::")[0].strip().strip("'")
    try:
        return float(v)
    except ValueError:
        return v


def _sqlite_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "contract_schema.sqlite3")
    db.init_db()
    c = sqlite3.connect(db.DB_PATH)
    try:
        return {t: {r[1]: (r[2].upper(), bool(r[3] or r[5]), _norm_default(r[4]))
                    for r in c.execute(f"pragma table_info({t})")} for t in TABLES}
    finally:
        c.close()


def _pg_columns(c):
    rows = c.execute("select table_name, column_name, data_type, is_nullable, column_default "
                     "from information_schema.columns where table_schema = 'public'").fetchall()
    out = {}
    for t, col, typ, nullable, default in rows:
        out.setdefault(t, {})[col] = (typ, nullable == "NO", _norm_default(default))
    return out


@pytest.fixture(scope="module")
def pg():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute((ROOT / "tests" / "sql" / "supabase_stub.sql").read_text(encoding="utf-8"))
    c.execute((ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8"))
    yield c
    c.close()


# ---------------------------------------------------------------- 1) 표 구조
@need_pg
def test_tables_match_between_sqlite_and_postgres(pg, tmp_path, monkeypatch):
    lite, remote = _sqlite_columns(tmp_path, monkeypatch), _pg_columns(pg)
    problems, seen_intended = [], set()
    for t in TABLES:
        pt = PG_TABLE.get(t, t)
        s, p = lite[t], remote.get(pt, {})
        if not p:
            problems.append(f"표 {t}: PostgreSQL 에 표 {pt} 가 없음")
            continue
        only_s = sorted(c for c in s.keys() - p.keys() if not _intended(t, c))
        only_p = sorted(c for c in p.keys() - s.keys() if not _intended(t, c))
        seen_intended |= {(t, c) for c in (s.keys() ^ p.keys()) if _intended(t, c)}
        if only_s:
            problems.append(f"표 {t}: SQLite 에만 있는 열 {only_s} (supabase/schema.sql 에 추가 필요)")
        if only_p:
            problems.append(f"표 {t}: PostgreSQL({pt}) 에만 있는 열 {only_p} (core/db.py init_db 에 추가 필요)")
        for col in sorted(s.keys() & p.keys()):
            (st, sn, sd), (ptyp, pn, pd) = s[col], p[col]
            if _intended(t, col):
                if (ptyp not in SQLITE_TYPES.get(st, ())) or sd != pd:
                    seen_intended.add((t, col))
                continue
            if ptyp not in SQLITE_TYPES.get(st, ()):
                problems.append(f"표 {t}.{col}: 타입 SQLite {st} ↔ PostgreSQL {ptyp}")
            if sn != pn:
                problems.append(f"표 {t}.{col}: NOT NULL SQLite {sn} ↔ PostgreSQL {pn}")
            if col != "id" and sd != pd:
                problems.append(f"표 {t}.{col}: 기본값 SQLite {sd!r} ↔ PostgreSQL {pd!r}")
    for key in INTENDED:                         # 의도된 차이 목록이 낡지 않게: 적어 둔 차이가 실제로 있는지
        hits = [k for k in seen_intended if k == key or (key[0] == "*" and k[1] == key[1])]
        if not hits:
            problems.append(f"INTENDED {key}: 적어 둔 차이가 실제로는 없음 → 목록에서 지우기")
    assert not problems, "\n" + "\n".join(problems)


@need_pg
def test_row_builders_and_fake_defaults_fit_real_tables(pg, tmp_path, monkeypatch):
    """core/rows.py 가 만드는 열은 두 저장소 표에 모두 있고, 가짜 백엔드의 DEFAULTS 는 실제 PostgreSQL 기본값과 같다."""
    lite, remote = _sqlite_columns(tmp_path, monkeypatch), _pg_columns(pg)
    built = {"wardrobe_items": wardrobe_row(1, {"name": "x", "category": "상의", "warmth": 1, "rain_ok": 0}, "now"),
             "purchase_candidates": candidate_row(1, "x", "상의", None, None, 1, 0, "", "", None, None, "now")}
    problems = []
    for t, row in built.items():
        for store, cols in (("SQLite", lite[t]), ("PostgreSQL", remote[t])):
            missing = sorted(row.keys() - cols.keys())
            if missing:
                problems.append(f"core/rows.py {t}: {store} 표에 없는 열 {missing}")
    for t, defaults in FAKE_DEFAULTS.items():
        for col, value in defaults.items():
            if col not in remote.get(t, {}):
                problems.append(f"tests/fake_supabase.py DEFAULTS {t}.{col}: PostgreSQL 표에 없는 열")
            elif _norm_default(value) != remote[t][col][2]:
                problems.append(f"tests/fake_supabase.py DEFAULTS {t}.{col}: 가짜 {value!r} ↔ PostgreSQL {remote[t][col][2]!r}")
    assert not problems, "\n" + "\n".join(problems)


# ---------------------------------------------------------------- 2) 값 보존
SET = object()                  # '값이 채워져 있어야 함' (시각처럼 미리 알 수 없는 값)


class _Json:
    """JSON 문자열로 저장되는 값: 풀어서 비교한다."""

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"JSON {self.value!r}"


ITEM = {"name": "계약 셔츠", "category": "상의", "subcategory": "셔츠", "color": "navy", "warmth": 4, "rain_ok": True,
        "material": "린넨", "image_path": "u/contract_1.jpg", "notes": "단추 하나 없음", "ai_category": "아우터",
        "ai_subcategory": "블레이저", "ai_confidence": 0.37}
ITEM_RETRY = {"name": "계약 슬랙스", "category": "하의", "subcategory": "슬랙스", "color": "beige", "warmth": 2,
              "rain_ok": False, "material": "면", "image_path": "u/contract_2.jpg", "notes": "밑단 수선",
              "ai_category": "하의", "ai_subcategory": "치노", "ai_confidence": 0.91}
ITEM_ONE = {"name": "계약 코트", "category": "아우터", "subcategory": "코트", "color": "gray", "warmth": 5, "rain_ok": True,
            "material": "울", "image_path": "u/contract_3.jpg", "notes": "드라이", "ai_category": "아우터",
            "ai_subcategory": "트렌치코트", "ai_confidence": 0.55}
EDIT = {"name": "계약 코트(고침)", "category": "아우터", "subcategory": "패딩", "color": "black", "warmth": 6,
        "rain_ok": False, "material": "나일론", "notes": "지퍼 고장"}
CAND = {"name": "계약 후보", "category": "아우터", "subcategory": "플리스", "color": "green", "warmth": 3, "rain_ok": True,
        "image_path": "u/cand_1.jpg", "link": "https://example.com/c/1", "ai_subcategory": "플리스", "ai_confidence": 0.66}
CAND_RETRY = {"name": "계약 후보(고침)", "category": "상의", "subcategory": "니트", "color": "red", "warmth": 4,
              "rain_ok": False, "image_path": "u/cand_2.jpg", "link": "https://example.com/c/2",
              "ai_subcategory": "가디건", "ai_confidence": 0.44}
WEATHER = {"purpose": "데이트", "temperature": 7.5, "apparent_temperature": 4.25, "humidity": 83.0, "precipitation": 2.5,
           "rain": True, "wind_speed": 13.5, "weather_source": "manual"}
RESULTS = [{"score": 0.8125, "pieces": [{"id": 1, "name": "계약 셔츠", "category": "상의"},
                                        {"id": 2, "name": "계약 슬랙스", "category": "하의"}],
            "reasons": ["체감 4℃에 두께가 맞습니다."], "components": {"weather": 0.9, "wet": 0.25}},
           {"score": 0.5, "pieces": [{"id": 3, "name": "계약 코트", "category": "아우터"}],
            "reasons": ["둘째 이유"], "components": {"weather": 0.5}}]


def _db_value(v):
    if isinstance(v, bool):
        return int(v)
    return v


class _Check:
    def __init__(self, name, read):
        self.name, self.read, self.problems = name, read, []

    def _compare(self, where, step, table, row, expected):
        for k, want in expected.items():
            if k not in row:
                self.problems.append(f"[{self.name}] {step}: {table}.{k} 열/값이 없음 (넣은 값 {want!r}) — {where}")
                continue
            got = row[k]
            if isinstance(got, uuid.UUID):
                got = str(got)
            if isinstance(got, float) and got != got:          # 읽기 함수의 빈 칸(NaN)
                got = None
            if want is SET:
                ok = got is not None
            elif isinstance(want, _Json):
                ok = got is not None and json.loads(got) == want.value
            else:
                ok = got == _db_value(want)
            if not ok:
                self.problems.append(f"[{self.name}] {step}: {table}.{k} 넣은 값 {want!r} → {where} 값 {got!r}")

    def stored(self, step, table, expected, **where):
        """DB 에 실제로 저장된 행."""
        row = self.read(table, **where)
        if row is None:
            self.problems.append(f"[{self.name}] {step}: {table} 에 {where} 행이 없음")
            return
        self._compare("저장된", step, table, row, expected)

    def api(self, step, what, row, expected):
        """저장소 읽기 함수가 돌려준 값."""
        if row is None:
            self.problems.append(f"[{self.name}] {step}: 읽기 함수 {what} 결과에 행이 없음")
            return
        self._compare(f"읽기 함수 {what}", step, what, row, expected)

    def same(self, step, got, want):
        if got != want:
            self.problems.append(f"[{self.name}] {step}: {want!r} 이어야 하는데 {got!r}")


def _scenario(store, uid, chk):
    r1, r2, r3 = (str(uuid.uuid4()) for _ in range(3))
    chk.stored("사용자 만들기", "users", {"name": "계약", "gender": "female", "cold_sensitivity": 0.7,
                                          "heat_sensitivity": 0.2, "baseline_decision_seconds": 0}, id=uid)
    store.update_user(uid, "male", 0.3, 0.9)
    chk.stored("프로필 고치기", "users", {"name": "계약", "gender": "male", "cold_sensitivity": 0.3,
                                          "heat_sensitivity": 0.9}, id=uid)

    # 사진 여러 장 저장: 새 행 → 같은 요청 id 재시도(아직 등록 상태라 지금 값으로 고침)
    [iid] = store.create_wardrobe_items(uid, [{**ITEM, "request_id": r1}])
    chk.stored("사진 여러 장 저장(새 행)", "wardrobe_items",
               {**ITEM, "request_id": r1, "active": 1, "deleted": 0, "edited_at": None, "seasons": None,
                "created_at": SET}, id=iid)
    kept = []
    chk.same("같은 요청 id 재시도의 id", store.create_wardrobe_items(uid, [{**ITEM_RETRY, "request_id": r1}],
                                                                    conflicts=kept), [iid])
    chk.same("같은 요청 id 재시도의 충돌", kept, [])
    chk.stored("사진 여러 장 저장(재시도로 고침)", "wardrobe_items", {**ITEM_RETRY, "request_id": r1, "edited_at": None},
               id=iid)

    # 한 벌 저장 → 정보 수정 → 계절 → 넣어 두기
    one = store.create_wardrobe_item(uid, **ITEM_ONE)
    chk.stored("한 벌 저장", "wardrobe_items", {**ITEM_ONE, "request_id": None, "active": 1}, id=one)
    store.update_wardrobe_item(one, uid, **EDIT)
    keep = {k: ITEM_ONE[k] for k in ("image_path", "ai_category", "ai_subcategory", "ai_confidence")}
    chk.stored("정보 수정", "wardrobe_items", {**EDIT, **keep, "edited_at": SET}, id=one)
    store.update_item_seasons(one, uid, ["봄", "겨울"])
    chk.stored("계절 직접 지정", "wardrobe_items", {"seasons": "봄|겨울"}, id=one)
    store.deactivate_item(one, uid)
    chk.stored("넣어 두기", "wardrobe_items", {"active": 0, "deleted": 0}, id=one)

    # 구매 후보: 새 행 → 같은 요청 id 재시도 → 샀어요 (DB 함수 buy_candidate)
    cid = store.create_candidate(uid, **CAND, request_id=r2)
    chk.stored("구매 후보 저장", "purchase_candidates", {**CAND, "request_id": r2, "deleted": 0, "created_at": SET},
               id=cid)
    kept = []
    chk.same("구매 후보 재시도의 id", store.create_candidate(uid, **CAND_RETRY, request_id=r2, conflicts=kept), cid)
    chk.same("구매 후보 재시도의 충돌", kept, [])
    chk.stored("구매 후보 재시도로 고침", "purchase_candidates", {**CAND_RETRY, "request_id": r2}, id=cid)
    bought = store.buy_candidate(cid, uid)
    bought_item = {k: CAND_RETRY[k] for k in ("name", "category", "subcategory", "color", "warmth", "rain_ok",
                                               "image_path", "ai_subcategory", "ai_confidence")}
    bought_item.update(notes=CAND_RETRY["link"], material="", ai_category=None, active=1, deleted=0, edited_at=None)
    chk.stored("샀어요 → 옷장", "wardrobe_items", bought_item, id=bought)
    chk.stored("샀어요 → 후보 끝냄", "purchase_candidates", {"deleted": 1}, id=cid)

    # 추천 한 묶음 저장 (DB 함수 save_recommendation_session) → 코디 저장 → 완료 → 피드백
    sid, rids = store.save_recommendation_session(uid, r3, results=RESULTS, **WEATHER)
    chk.stored("추천 세션 저장", "recommendation_sessions",
               {**WEATHER, "request_id": r3, "started_at": SET, "completed_at": None}, id=sid)
    chk.same("추천 저장 개수", len(rids), len(RESULTS))
    for rank, (rid, r) in enumerate(zip(rids, RESULTS), 1):
        chk.stored(f"추천 {rank}위 저장", "recommendations",
                   {"session_id": sid, "rank": rank, "score": r["score"], "pieces_json": _Json(r["pieces"]),
                    "reasons_json": _Json(r["reasons"]), "components_json": _Json(r["components"]),
                    "created_at": SET}, id=rid)
    store.save_outfit(uid, rids[1])
    store.complete_recommendation_session(sid)
    chk.stored("코디 저장", "saved_outfits", {"recommendation_id": rids[1], "saved_at": SET}, recommendation_id=rids[1])
    chk.stored("세션 완료", "recommendation_sessions", {"completed_at": SET}, id=sid)
    store.upsert_feedback(uid, rids[0], rating="like", worn=True, thermal_feedback=1, satisfaction=2)
    fb = {"rating": "like", "worn": 1, "thermal_feedback": 1, "satisfaction": 2, "updated_at": SET}
    chk.stored("피드백 저장", "feedback", fb, recommendation_id=rids[0])
    store.upsert_feedback(uid, rids[0], satisfaction=5)                   # 일부만 고쳐도 나머지는 그대로
    chk.stored("피드백 일부 고치기", "feedback", {**fb, "satisfaction": 5}, recommendation_id=rids[0])

    # 저장소 읽기 함수로 다시 읽어도 같은지
    chk.api("프로필 읽기", "get_user", store.get_user(uid),
            {"name": "계약", "gender": "male", "cold_sensitivity": 0.3, "heat_sensitivity": 0.9})
    items = {x["id"]: x for x in store.list_wardrobe_items(uid, active_only=False)}
    chk.api("옷장 읽기", "list_wardrobe_items", items.get(iid), {**ITEM_RETRY, "request_id": r1})
    chk.api("옷장 읽기", "list_wardrobe_items", items.get(one), {**EDIT, **keep, "seasons": "봄|겨울", "active": 0})
    chk.api("옷장 읽기", "list_wardrobe_items", items.get(bought), bought_item)
    logs = store.export_user_logs(uid).to_dict("records")
    first = next((x for x in logs if x.get("rank") == 1), None)
    chk.api("추천 로그 읽기", "export_user_logs", first,
            {**WEATHER, "score": RESULTS[0]["score"], "completed_at": SET, "rating": "like", "worn": 1,
             "thermal_feedback": 1, "satisfaction": 5, "outfit": "계약 셔츠 + 계약 슬랙스"})
    saved = store.list_saved_outfits(uid)
    chk.same("저장 코디 개수", len(saved), 1)
    chk.api("저장 코디 읽기", "list_saved_outfits", saved[0] if saved else None,
            {"rank": 2, "purpose": WEATHER["purpose"]})


def _sqlite_reader():
    def read(table, **where):
        sql = f"SELECT * FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in where)
        with db.conn() as c:
            row = c.execute(sql, list(where.values())).fetchone()
        return dict(row) if row else None
    return read


def _pg_reader(c):
    def read(table, **where):
        sql = f"select * from {PG_TABLE.get(table, table)} where " + " and ".join(f"{k} = %s" for k in where)
        cur = c.execute(sql, list(where.values()))
        row = cur.fetchone()
        return dict(zip([d.name for d in cur.description], row)) if row else None
    return read


def test_values_survive_every_write_path_on_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "contract_values.sqlite3")
    db.init_db()
    uid = db.create_user("계약", "female", 0.7, 0.2)
    chk = _Check("SQLite", _sqlite_reader())
    _scenario(db, uid, chk)
    assert not chk.problems, "\n" + "\n".join(chk.problems)


@need_pg
def test_values_survive_every_write_path_on_postgres(pg):
    """앱의 CloudStore → 실제 PostgreSQL(DB 함수·RLS) → 표·뷰·DB 함수로 다시 읽기."""
    uid = str(uuid.uuid4())
    pg.execute("insert into auth.users(id, email) values (%s, %s)", (uid, f"{uid}@t"))
    with As(pg, uid):
        store = CloudStore(_PgClient(pg), uid)
        store.create_user("계약", "female", 0.7, 0.2)
        chk = _Check("PostgreSQL", _pg_reader(pg))
        _scenario(store, uid, chk)
    assert not chk.problems, "\n" + "\n".join(chk.problems)
