"""테스트용 가짜 Supabase (메모리). core/cloud_store.py, core/media.py, 로그인 화면이 쓰는 만큼만 흉내 낸다.

실제 서비스의 RLS 정책(supabase/schema.sql)과 같은 규칙을 여기서도 지킨다:
  - 로그인 안 하면 아무 표도 못 읽고 못 씀
  - 읽기·수정·삭제는 user_id(프로필은 id)가 내 id인 행만
  - 쓰기는 user_id 가 내 id여야 하고, 하위 표는 가리키는 상위 행도 내 것이어야 함
  - 사진은 첫 폴더가 내 id인 경로만
진짜 RLS 확인은 PostgreSQL에서 따로 한다. 이 파일은 앱 코드의 흐름을 빠르게 검사하는 용도.
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace


class FakeAPIError(Exception):
    pass


OWNER_COL = {"profiles": "id"}
PARENT_CHECK = {  # 표: (가리키는 열, 상위 표)
    "recommendations": ("session_id", "recommendation_sessions"),
    "saved_outfits": ("recommendation_id", "recommendations"),
    "feedback": ("recommendation_id", "recommendations"),
}
DEFAULTS = {  # supabase/schema.sql 의 기본값 (DB는 빠진 열도 채워서 돌려준다)
    "wardrobe_items": {"seasons": None, "active": 1, "deleted": 0},
    "purchase_candidates": {"deleted": 0},
    "recommendation_sessions": {"completed_at": None, "decision_seconds": None},
}
UNIQUE = {"saved_outfits": ("user_id", "recommendation_id"), "feedback": ("user_id", "recommendation_id")}


class FakeBackend:
    """Supabase 프로젝트 하나 (여러 클라이언트가 공유)."""

    def __init__(self, autoconfirm=True):
        self.tables = {}
        self.next_id = {}
        self.users = {}       # email -> {"id", "password"}
        self.objects = {}     # "bucket/path" -> bytes
        self.autoconfirm = autoconfirm
        self.calls = []

    def rows(self, table):
        return self.tables.setdefault(table, [])


class _Result(SimpleNamespace):
    pass


class _Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op, self.payload, self.filters = "select", None, []
        self.order_by, self.on_conflict, self.ignore_dup = None, None, False

    # 조건
    def select(self, cols="*", **kw):
        self.op = "select"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def is_(self, col, val):
        assert val == "null"
        self.filters.append(lambda r: r.get(col) is None)
        return self

    def order(self, col, desc=False):
        self.order_by = (col, desc)
        return self

    # 쓰기
    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False):
        self.op, self.payload, self.on_conflict, self.ignore_dup = "upsert", payload, on_conflict, ignore_duplicates
        return self

    def _visible(self, uid):
        col = OWNER_COL.get(self.table, "user_id")
        return [r for r in self.client.backend.rows(self.table) if r.get(col) == uid]

    def _check_row(self, row, uid):
        col = OWNER_COL.get(self.table, "user_id")
        if row.get(col) != uid:
            raise FakeAPIError(f"new row violates row-level security policy for table \"{self.table}\"")
        if self.table in PARENT_CHECK:
            fk, parent = PARENT_CHECK[self.table]
            if not any(p["id"] == row.get(fk) and p.get("user_id") == uid for p in self.client.backend.rows(parent)):
                raise FakeAPIError(f"new row violates row-level security policy for table \"{self.table}\"")

    def execute(self):
        b, uid = self.client.backend, self.client.uid
        b.calls.append((self.table, self.op))
        if uid is None:
            raise FakeAPIError("permission denied (not signed in)")
        rows = self._visible(uid)
        for f in self.filters:
            rows = [r for r in rows if f(r)]
        if self.op == "select":
            out = copy.deepcopy(rows)
            if self.order_by:
                col, desc = self.order_by
                out.sort(key=lambda r: r.get(col), reverse=desc)
            return _Result(data=out)
        if self.op == "update":
            for r in rows:
                r.update(copy.deepcopy(self.payload))
            return _Result(data=copy.deepcopy(rows))
        if self.op == "delete":                       # RLS: 내 행 중 조건에 맞는 것만 지워진다
            gone = [id(r) for r in rows]
            b.tables[self.table] = [r for r in b.rows(self.table) if id(r) not in gone]
            return _Result(data=copy.deepcopy(rows))
        payload = self.payload if isinstance(self.payload, list) else [self.payload]
        created = []
        for row in payload:
            row = copy.deepcopy(row)
            self._check_row(row, uid)
            if self.table in UNIQUE:
                keys = UNIQUE[self.table]
                if any(all(r.get(k) == row.get(k) for k in keys) for r in b.rows(self.table)):
                    if self.op == "upsert" and self.ignore_dup:
                        continue
                    raise FakeAPIError("duplicate key value violates unique constraint")
            if self.table == "profiles":
                if any(r["id"] == row["id"] for r in b.rows(self.table)):
                    raise FakeAPIError("duplicate key value violates unique constraint")
            else:
                b.next_id[self.table] = b.next_id.get(self.table, 0) + 1
                row["id"] = b.next_id[self.table]
            for col, default in DEFAULTS.get(self.table, {}).items():
                row.setdefault(col, default)
            b.rows(self.table).append(row)
            created.append(copy.deepcopy(row))
        return _Result(data=created)


class _Auth:
    def __init__(self, client):
        self.client = client
        self.session = None

    def sign_up(self, cred):
        b = self.client.backend
        email = cred["email"].lower()
        if len(cred["password"]) < 6:
            raise FakeAPIError("Password should be at least 6 characters.")
        if email in b.users:
            raise FakeAPIError("User already registered")
        b.users[email] = {"id": str(uuid.uuid4()), "password": cred["password"], "confirmed": b.autoconfirm}
        user = SimpleNamespace(id=b.users[email]["id"], email=email)
        if not b.autoconfirm:
            return SimpleNamespace(user=user, session=None)
        self.session = SimpleNamespace(user=user, access_token="tok-" + user.id)
        return SimpleNamespace(user=user, session=self.session)

    def sign_in_with_password(self, cred):
        u = self.client.backend.users.get(cred["email"].lower())
        if not u or u["password"] != cred["password"]:
            raise FakeAPIError("Invalid login credentials")
        if not u["confirmed"]:
            raise FakeAPIError("Email not confirmed")
        user = SimpleNamespace(id=u["id"], email=cred["email"].lower())
        self.session = SimpleNamespace(user=user, access_token="tok-" + user.id)
        return SimpleNamespace(user=user, session=self.session)

    def get_session(self):
        return self.session

    def sign_out(self):
        self.session = None


class _Bucket:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def _check(self, path):
        uid = self.client.uid
        if uid is None or path.split("/", 1)[0] != uid:
            raise FakeAPIError("new row violates row-level security policy (storage)")

    def upload(self, path, data, file_options=None):
        self._check(path)
        key = f"{self.name}/{path}"
        if key in self.client.backend.objects:
            raise FakeAPIError("The resource already exists")
        self.client.backend.objects[key] = bytes(data)
        return SimpleNamespace(path=path)

    def download(self, path):
        self._check(path)
        key = f"{self.name}/{path}"
        if key not in self.client.backend.objects:
            raise FakeAPIError("Object not found")
        return self.client.backend.objects[key]

    def remove(self, paths):
        for path in paths:
            self._check(path)
            self.client.backend.objects.pop(f"{self.name}/{path}", None)
        return [SimpleNamespace(name=x) for x in paths]

    def create_signed_url(self, path, expires_in):
        self._check(path)
        return {"signedURL": f"https://fake.supabase.co/storage/v1/object/sign/{self.name}/{path}?token=t{expires_in}",
                "signedUrl": None}


class _Storage:
    def __init__(self, client):
        self.client = client

    def from_(self, bucket):
        return _Bucket(self.client, bucket)


class FakeClient:
    def __init__(self, backend):
        self.backend = backend
        self.auth = _Auth(self)
        self.storage = _Storage(self)

    @property
    def uid(self):
        return self.auth.session.user.id if self.auth.session else None

    def table(self, name):
        return _Query(self, name)


def signed_in_client(backend, email, password="secret123"):
    c = FakeClient(backend)
    try:
        c.auth.sign_up({"email": email, "password": password})
    except FakeAPIError:
        c.auth.sign_in_with_password({"email": email, "password": password})
    return c
