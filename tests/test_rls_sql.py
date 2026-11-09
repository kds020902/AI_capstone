"""supabase/schema.sql 의 행 단위 보안(RLS)을 실제 PostgreSQL에서 확인한다.

Supabase 가 요청마다 하는 것처럼 `authenticated` 역할 + JWT의 sub(사용자 id)를 설정하고 SQL을 실행한다.
PostgreSQL 이 있어야 해서 기본으로는 건너뛴다. 실행하려면:
  AI_CLOSET_TEST_PG_DSN="postgresql://postgres@127.0.0.1:54329/postgres" pytest tests/test_rls_sql.py
(빈 DB에 tests/sql/supabase_stub.sql → supabase/schema.sql 을 자동으로 적용한다)
"""
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import db
from core.cloud_store import CloudStore

DSN = os.environ.get("AI_CLOSET_TEST_PG_DSN")
psycopg = pytest.importorskip("psycopg") if DSN else None
pytestmark = pytest.mark.skipif(not DSN, reason="AI_CLOSET_TEST_PG_DSN 이 없어 PostgreSQL RLS 검사를 건너뜀")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(DSN, autocommit=True)
    c.execute((ROOT / "tests" / "sql" / "supabase_stub.sql").read_text(encoding="utf-8"))
    c.execute((ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8"))
    yield c
    c.close()


@pytest.fixture
def users(conn):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute("insert into auth.users(id, email) values (%s, %s), (%s, %s)", (a, f"{a}@t", b, f"{b}@t"))
    return a, b


class As:
    """한 트랜잭션 동안 로그인 사용자(uid)로 행동한다. 끝나면 되돌린다(rollback)."""

    def __init__(self, conn, uid):
        self.conn, self.uid = conn, uid

    def __enter__(self):
        self.tx = self.conn.transaction(force_rollback=False)
        self.tx.__enter__()
        _become(self.conn, self.uid)
        return self

    def q(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        return cur.fetchall() if cur.description else cur.rowcount

    def __exit__(self, *exc):
        return self.tx.__exit__(*exc)


def _become(conn, uid):
    """지금 거래 안에서 로그인 사용자(uid)가 된다 (Supabase 가 요청마다 하는 것처럼)."""
    conn.execute("set local role authenticated")
    claims = json.dumps({"sub": uid, "role": "authenticated"}) if uid else ""
    conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))


def _item(uid, name="셔츠"):
    return ("insert into wardrobe_items(user_id, name, category, warmth, rain_ok, created_at) "
            "values (%s, %s, '상의', 2, 0, 'now') returning id", (uid, name))


def test_each_user_sees_only_own_rows(conn, users):
    a, b = users
    with As(conn, a) as s:
        s.q("insert into profiles(id, name, created_at) values (%s, 'A', 'now')", (a,))
        item_id = s.q(*_item(a))[0][0]
    with As(conn, b) as s:
        s.q("insert into profiles(id, name, created_at) values (%s, 'B', 'now')", (b,))
        assert s.q("select id from wardrobe_items") == []
        assert [r[0] for r in s.q("select id from profiles")] == [uuid.UUID(b)]
        # 남의 행 수정·삭제는 0건 처리 (오류 없이 안 보임)
        assert s.q("update wardrobe_items set name='해킹' where id=%s", (item_id,)) == 0
        assert s.q("delete from wardrobe_items where id=%s", (item_id,)) == 0
    with As(conn, a) as s:
        assert s.q("select name from wardrobe_items where id=%s", (item_id,)) == [("셔츠",)]


def test_cannot_write_rows_for_someone_else(conn, users):
    a, b = users
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with As(conn, b) as s:
            s.q(*_item(a, "남의 옷"))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with As(conn, b) as s:
            s.q("insert into profiles(id, name, created_at) values (%s, 'X', 'now')", (a,))


def test_child_rows_must_point_to_own_parents(conn, users):
    a, b = users
    with As(conn, a) as s:
        sid = s.q("insert into recommendation_sessions(user_id, purpose, temperature, humidity, precipitation, rain,"
                  " wind_speed, weather_source, started_at) values (%s,'등교',12,60,0,0,8,'manual','now') returning id",
                  (a,))[0][0]
        rid = s.q("insert into recommendations(user_id, session_id, rank, score, pieces_json, reasons_json,"
                  " components_json, created_at) values (%s,%s,1,0.9,'[]','[]','{}','now') returning id",
                  (a, sid))[0][0]
    for sql, args in [
        ("insert into recommendations(user_id, session_id, rank, score, pieces_json, reasons_json, components_json,"
         " created_at) values (%s,%s,1,0.1,'[]','[]','{}','now')", (b, sid)),
        ("insert into saved_outfits(user_id, recommendation_id, saved_at) values (%s,%s,'now')", (b, rid)),
        ("insert into feedback(user_id, recommendation_id, rating, updated_at) values (%s,%s,'like','now')", (b, rid)),
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with As(conn, b) as s:
                s.q(sql, args)
    with As(conn, a) as s:   # 내 추천에는 저장·피드백 가능, 두 번 저장은 한 번만
        s.q("insert into saved_outfits(user_id, recommendation_id, saved_at) values (%s,%s,'now') "
            "on conflict (user_id, recommendation_id) do nothing", (a, rid))
        s.q("insert into saved_outfits(user_id, recommendation_id, saved_at) values (%s,%s,'now') "
            "on conflict (user_id, recommendation_id) do nothing", (a, rid))
        assert s.q("select count(*) from saved_outfits") == [(1,)]


def test_owner_can_delete_own_rows_and_profile(conn, users):
    """scripts/check_supabase.py 가 시험 데이터를 지울 수 있는지. 사용자를 지우면 모든 행이 함께 지워진다."""
    a, _ = users
    with As(conn, a) as s:
        s.q("insert into profiles(id, name, created_at) values (%s, 'A', 'now')", (a,))
        item_id = s.q(*_item(a))[0][0]
    with As(conn, a) as s:
        assert s.q("delete from wardrobe_items where id=%s", (item_id,)) == 1
        assert s.q("delete from profiles where id=%s", (a,)) == 1
    with As(conn, a) as s:
        s.q(*_item(a))
    conn.execute("delete from auth.users where id=%s", (a,))      # 대시보드에서 사용자 삭제
    assert conn.execute("select count(*) from wardrobe_items where user_id=%s", (a,)).fetchone() == (0,)


def test_signed_out_visitor_gets_nothing(conn, users):
    a, _ = users
    with As(conn, a) as s:
        s.q(*_item(a))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):   # anon 역할에는 표 권한 자체가 없다
        with conn.transaction():
            conn.execute("set local role anon")
            conn.execute("select * from wardrobe_items")
    with As(conn, None) as s:                                   # 토큰 없는 authenticated 도 0행
        assert s.q("select * from wardrobe_items") == []


def test_storage_folder_policy(conn, users):
    a, b = users
    with As(conn, a) as s:
        s.q("insert into storage.objects(bucket_id, name, owner) values ('wardrobe', %s, %s)", (f"{a}/x.jpg", a))
    with As(conn, b) as s:
        assert s.q("select name from storage.objects") == []
        assert s.q("delete from storage.objects where name=%s", (f"{a}/x.jpg",)) == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with As(conn, b) as s:
            s.q("insert into storage.objects(bucket_id, name, owner) values ('wardrobe', %s, %s)", (f"{a}/y.jpg", b))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):   # 다른 버킷 이름으로 우회도 안 됨
        with As(conn, b) as s:
            s.q("insert into storage.objects(bucket_id, name, owner) values ('wardrobe', %s, %s)", ("x.jpg", b))
    assert conn.execute("select public, file_size_limit from storage.buckets where id='wardrobe'").fetchone() == (False, 5242880)


# ------------------------------------------------------------ 한 거래로 처리하는 DB 함수 (rpc)
def _jsonish(v):
    return str(v) if isinstance(v, uuid.UUID) else v          # PostgREST 는 uuid 를 문자열(JSON)로 준다


class _PgQuery:
    """CloudStore 가 쓰는 만큼의 PostgREST 쿼리(select·insert·update·upsert, eq·in_·is_·order·range·count)를
    실제 SQL 로 바꿔 지금 거래(로그인 사용자의 RLS 적용) 안에서 실행한다. 가짜 백엔드가 아니라 실제 DB 동작 확인용."""

    def __init__(self, conn, table):
        self.conn, self.table = conn, table
        self.op, self.cols, self.payload, self.where, self.args = "select", "*", None, [], []
        self.orders, self.window, self.count, self.conflict = [], None, None, None

    def select(self, cols="*", count=None):
        self.cols, self.count = cols, count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        assert ignore_duplicates
        self.op, self.payload, self.conflict = "insert", payload, on_conflict
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def eq(self, col, val):
        self.where.append(f"{col} = %s")
        self.args.append(val)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.where.append(f"{col}::text = any(%s)" if all(isinstance(v, str) for v in vals) else f"{col} = any(%s)")
        self.args.append(vals)
        return self

    def is_(self, col, val):
        assert val == "null"
        self.where.append(f"{col} is null")
        return self

    def order(self, col, desc=False):
        self.orders.append(f"{col} {'desc' if desc else 'asc'}")
        return self

    def range(self, start, end):
        assert self.window is None
        self.window = (start, end)
        return self

    def _rows(self, cur):
        names = [d.name for d in cur.description]
        return [{k: _jsonish(v) for k, v in zip(names, r)} for r in cur.fetchall()]

    def execute(self):
        where = (" where " + " and ".join(self.where)) if self.where else ""
        if self.op == "select":
            sql = f"select {self.cols} from public.{self.table}{where}"
            total = (self.conn.execute(f"select count(*) from ({sql}) q", self.args).fetchone()[0]
                     if self.count == "exact" else None)
            if self.orders:
                sql += " order by " + ", ".join(self.orders)
            if self.window:
                sql += f" offset {self.window[0]} limit {self.window[1] - self.window[0] + 1}"
            return SimpleNamespace(data=self._rows(self.conn.execute(sql, self.args)), count=total)
        if self.op == "update":
            sets = ", ".join(f"{k} = %s" for k in self.payload)
            cur = self.conn.execute(f"update public.{self.table} set {sets}{where} returning *",
                                    [*self.payload.values(), *self.args])
            return SimpleNamespace(data=self._rows(cur), count=None)
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        cols = list(rows[0])
        values = ", ".join("(" + ", ".join(["%s"] * len(cols)) + ")" for _ in rows)
        sql = f"insert into public.{self.table} ({', '.join(cols)}) values {values}"
        if self.conflict:
            sql += f" on conflict ({self.conflict}) do nothing"
        cur = self.conn.execute(sql + " returning *", [r.get(c) for r in rows for c in cols])
        return SimpleNamespace(data=self._rows(cur), count=None)


class _PgClient:
    """CloudStore 의 client 자리에 넣는다: table(...) 은 위 _PgQuery, rpc(이름, 인자).execute() 는 실제 함수 호출.
    함수가 돌려주는 표를 PostgREST 처럼 [{열: 값}] 으로 돌려준다."""

    def __init__(self, conn):
        self.conn = conn

    def table(self, name):
        return _PgQuery(self.conn, name)

    def rpc(self, fn, params):
        from psycopg.types.json import Jsonb
        sql = f"select * from public.{fn}({', '.join(f'{k} => %s' for k in params)})"
        args = [Jsonb(v) if isinstance(v, (list, dict)) else v for v in params.values()]

        def execute():
            cur = self.conn.execute(sql, args)
            cols = [d.name for d in cur.description]
            return SimpleNamespace(data=[dict(zip(cols, r)) for r in cur.fetchall()])
        return SimpleNamespace(execute=execute)


CAND = ("네이비 플리스", "아우터", "플리스", "navy", 3, 0, "https://example.com/p/1", "플리스", 0.83)
ITEM_COLS = ("name", "category", "subcategory", "color", "warmth", "rain_ok", "material", "image_path", "notes",
             "ai_category", "ai_subcategory", "ai_confidence", "seasons", "active", "deleted")
REQ = "6f1c1e0a-3a52-4c63-9a0e-2f1d1a0b9c01"
RESULTS = [{"score": 0.91 - i / 10, "pieces": [{"id": i, "name": f"옷{i}"}], "reasons": [f"이유{i}"],
            "components": {"weather": 0.9}} for i in range(3)]


def _candidate(s, uid):
    return s.q("insert into purchase_candidates(user_id, name, category, subcategory, color, warmth, rain_ok, link,"
               " ai_subcategory, ai_confidence, created_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'now') returning id",
               (uid, *CAND))[0][0]


def _counts(s):
    return s.q("select (select count(*) from wardrobe_items), (select count(*) from purchase_candidates where deleted=0),"
               " (select count(*) from recommendation_sessions), (select count(*) from recommendations)")[0]


def test_buy_candidate_function_rolls_back_together(conn, users):
    """옷장 넣기가 실패하면 같은 거래의 후보 빼기도 되돌아가 둘 다 원래대로 남는다 (가짜 백엔드가 아니라 실제 거래)."""
    a, _ = users
    with conn.transaction(force_rollback=True):                       # 이 시험이 만든 것은 끝나면 모두 되돌림
        conn.execute("create function public.t_fail() returns trigger language plpgsql as "
                     "$$ begin raise exception '옷장 저장 실패'; end $$")
        conn.execute("create trigger t_fail before insert on public.wardrobe_items "
                     "for each row execute function public.t_fail()")
        _become(conn, a)
        s = As(conn, a)
        cid = _candidate(s, a)
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():                                   # 요청 하나 = 함수 한 번 = 거래 하나
                CloudStore(_PgClient(conn), a).buy_candidate(cid, a)
        assert s.q("select deleted from purchase_candidates where id=%s", (cid,)) == [(0,)]
        assert s.q("select count(*) from wardrobe_items") == [(0,)]


def test_buy_candidate_function_buys_once_and_only_own(conn, users, tmp_path):
    a, b = users
    with As(conn, a) as s:
        cid = _candidate(s, a)
        store = CloudStore(_PgClient(conn), a)
        new_id = store.buy_candidate(cid, a)
        assert new_id and store.buy_candidate(cid, a) is None          # 다시 보내도(응답 유실 후 재시도) 한 벌만
        assert _counts(s)[:2] == (1, 0)
        cloud_row = dict(zip(ITEM_COLS, s.q(f"select {', '.join(ITEM_COLS)} from wardrobe_items where id=%s",
                                            (new_id,))[0]))
        cid2 = _candidate(s, a)
    with As(conn, b) as s:                                             # 남의 후보는 못 산다 (빈 결과, 아무것도 안 바뀜)
        assert CloudStore(_PgClient(conn), b).buy_candidate(cid2, b) is None
        assert _counts(s)[:2] == (0, 0)
    with As(conn, None) as s:                                          # 로그인 토큰이 없으면 아무것도 못 산다
        assert CloudStore(_PgClient(conn), "x").buy_candidate(cid2, "x") is None
    with As(conn, a) as s:
        assert s.q("select deleted from purchase_candidates where id=%s", (cid2,)) == [(0,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):         # 로그인 안 한 방문자는 함수 실행 권한도 없다
        with conn.transaction():
            conn.execute("set local role anon")
            conn.execute("select * from public.buy_candidate(%s, 'now')", (cid2,))

    # 같은 후보를 로컬 SQLite 판으로 사면 옷장에 들어가는 값이 같다
    db.DB_PATH = tmp_path / "buy.sqlite3"
    db.init_db()
    uid = db.create_user("나", "male", 0, 0)
    local_id = db.buy_candidate(db.create_candidate(uid, *CAND[:6], link=CAND[6], ai_subcategory=CAND[7],
                                                    ai_confidence=CAND[8]), uid)
    local_row = {k: v for k, v in db.list_wardrobe_items(uid)[0].items() if k in ITEM_COLS}
    assert local_id and local_row == cloud_row


def test_save_recommendation_session_function_is_one_bundle(conn, users, tmp_path):
    a, b = users
    with As(conn, a) as s:
        store = CloudStore(_PgClient(conn), a)
        args = ("등교", 12.0, 11.0, 60.0, 0.0, False, 8.0, "manual")
        sid, rids = store.save_recommendation_session(a, REQ, *args, RESULTS)
        assert len(rids) == 3
        assert store.save_recommendation_session(a, REQ, *args, RESULTS) == (sid, rids)   # 재시도: 그대로
        assert _counts(s)[2:] == (1, 3)
        cloud_recs = s.q("select rank, score, pieces_json, reasons_json, components_json from recommendations "
                         "where session_id=%s order by rank", (sid,))
        # 셋째 결과가 잘못돼 중간에 실패하면 세션도 앞 결과도 남지 않는다
        bad = RESULTS[:2] + [{**RESULTS[2], "score": None}]
        with pytest.raises(psycopg.errors.NotNullViolation):
            with conn.transaction():
                store.save_recommendation_session(a, str(uuid.uuid4()), *args, bad)
        assert _counts(s)[2:] == (1, 3)
        empty_sid, empty_rids = store.save_recommendation_session(a, str(uuid.uuid4()), *args, [])   # 코디 없음
        assert empty_sid != sid and empty_rids == []
    with As(conn, b) as s:                         # 같은 요청 id 라도 B 는 자기 세션이 따로 생기고 A 것은 안 보인다
        sid_b, rids_b = CloudStore(_PgClient(conn), b).save_recommendation_session(b, REQ, *args, RESULTS[:1])
        assert sid_b != sid and len(rids_b) == 1
        assert _counts(s)[2:] == (1, 1)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):         # 토큰이 없으면 RLS 가 막아 저장되지 않는다
        with As(conn, None):
            CloudStore(_PgClient(conn), "x").save_recommendation_session("x", REQ, *args, RESULTS)

    # 로컬 SQLite 판도 같은 묶음·같은 값으로 저장한다
    db.DB_PATH = tmp_path / "rec.sqlite3"
    db.init_db()
    uid = db.create_user("나", "male", 0, 0)
    lsid, lrids = db.save_recommendation_session(uid, REQ, *args, RESULTS)
    assert db.save_recommendation_session(uid, REQ, *args, RESULTS) == (lsid, lrids) and len(lrids) == 3
    with db.conn() as c:
        local_recs = [tuple(r) for r in c.execute("select rank, score, pieces_json, reasons_json, components_json "
                                                   "from recommendations where session_id=? order by rank", (lsid,))]
    assert local_recs == cloud_recs


def test_request_id_insert_once_per_user(conn, users):
    """사진 등록·구매 후보의 '같은 요청은 한 번만'(앱이 보내는 upsert = on conflict do nothing)이 RLS 아래에서 동작하고,
    다른 사용자는 같은 요청 id라도 따로 저장된다. 요청 id가 없는 예전 방식은 매번 새로 들어간다."""
    a, b = users
    once = ("insert into {t}(user_id, name, category, warmth, rain_ok, created_at, request_id) "
            "values (%s, '셔츠', '상의', 2, 0, 'now', %s) on conflict (user_id, request_id) do nothing")
    for table in ("wardrobe_items", "purchase_candidates"):
        with As(conn, a) as s:
            assert s.q(once.format(t=table), (a, REQ)) == 1
            assert s.q(once.format(t=table), (a, REQ)) == 0                  # 재시도: 안 들어감
            s.q(once.format(t=table), (a, None))
            s.q(once.format(t=table), (a, None))                             # 요청 id 없음: 매번 새로
            assert s.q(f"select count(*) from {table}") == [(3,)]
        with As(conn, b) as s:
            assert s.q(once.format(t=table), (b, REQ)) == 1
            assert s.q(f"select count(*) from {table}") == [(1,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):            # 남의 이름으로는 여전히 못 넣음
            with As(conn, b) as s:
                s.q(once.format(t=table), (a, str(uuid.uuid4())))


def test_cloud_store_on_real_postgres_matches_sqlite(conn, tmp_path, monkeypatch):
    """앱의 Supabase 저장소 코드(CloudStore)를 실제 PostgreSQL(뷰·DB 함수·RLS)에 그대로 돌려 SQLite 판과 결과를 비교한다.
    한 번에 3줄씩만 받게 해서(PAGE=3) 반환 한도보다 많은 기록(옷 29벌·로그 8줄)도 끝까지 나눠 받는지 본다.
    추천의 동점 순서가 옷 id 로 정해지므로, id 가 SQLite 처럼 1부터 시작하도록 새 DB를 따로 만들어 쓴다."""
    from psycopg.conninfo import make_conninfo

    from core import cloud_store
    from tests.test_cloud_store import _scenario
    monkeypatch.setattr(cloud_store, "PAGE", 3)
    db.DB_PATH = tmp_path / "parity.sqlite3"
    db.init_db()
    local = _scenario(db, db.create_user("동일", "female", 0.6, 0.1))

    name = f"closet_parity_{uuid.uuid4().hex[:8]}"
    conn.execute(f"create database {name}")
    try:
        with psycopg.connect(make_conninfo(DSN, dbname=name), autocommit=True) as c:
            c.execute((ROOT / "tests" / "sql" / "supabase_stub.sql").read_text(encoding="utf-8"))
            c.execute((ROOT / "supabase" / "schema.sql").read_text(encoding="utf-8"))
            a, b = str(uuid.uuid4()), str(uuid.uuid4())
            c.execute("insert into auth.users(id, email) values (%s, 'a@t'), (%s, 'b@t')", (a, b))
            with As(c, a):
                store = CloudStore(_PgClient(c), a)
                store.create_user("동일", "female", 0.6, 0.1)
                remote = _scenario(store, a)
            with As(c, b):                                             # B 에게는 A 의 기록이 뷰로도 안 보인다
                sb = CloudStore(_PgClient(c), b)
                assert sb.list_saved_outfits(b) == [] and sb.export_user_logs(b).empty
                assert sb.user_kpi_stats(b) == {"completed_sessions": 0, "top3_acceptance_rate": None}
                assert sb.disliked_item_sets(b) == set() and sb.list_wardrobe_items(b) == []
    finally:
        conn.execute(f"drop database if exists {name}")
    for key in local:
        assert remote[key] == local[key], key
    assert len(local["log_rows"]) == 8 and len(local["items"]) == 29
