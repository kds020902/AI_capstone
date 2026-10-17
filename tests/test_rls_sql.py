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

import pytest

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
        self.conn.execute("set local role authenticated")
        claims = json.dumps({"sub": self.uid, "role": "authenticated"}) if self.uid else ""
        self.conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        return self

    def q(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        return cur.fetchall() if cur.description else cur.rowcount

    def __exit__(self, *exc):
        return self.tx.__exit__(*exc)


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
