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
import json
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
    "wardrobe_items": {"seasons": None, "active": 1, "deleted": 0, "request_id": None, "edited_at": None},
    "purchase_candidates": {"deleted": 0, "request_id": None},
    "recommendation_sessions": {"completed_at": None, "decision_seconds": None, "request_id": None},
}
UNIQUE = {"saved_outfits": ("user_id", "recommendation_id"), "feedback": ("user_id", "recommendation_id"),
          "wardrobe_items": ("user_id", "request_id"), "purchase_candidates": ("user_id", "request_id"),
          "recommendation_sessions": ("user_id", "request_id")}   # 값이 NULL 이면 겹쳐도 됨 (PostgreSQL 과 같음)


def _view_rows(b, uid, name):
    """supabase/schema.sql 의 뷰를 흉내 낸다 (security_invoker: 아래 표에 RLS 가 적용돼 내 행만 보임)."""
    mine = {t: [r for r in b.rows(t) if r.get("user_id") == uid]
            for t in ("recommendation_sessions", "recommendations", "saved_outfits", "feedback")}
    sessions = {s["id"]: s for s in mine["recommendation_sessions"]}
    recs = {r["id"]: r for r in mine["recommendations"]}
    if name == "saved_outfit_rows":
        out = []
        for s in mine["saved_outfits"]:
            r = recs.get(s["recommendation_id"])
            rs = sessions.get(r["session_id"]) if r else None
            if rs:
                out.append({"id": s["id"], "user_id": uid, "saved_at": s["saved_at"], "rank": r["rank"],
                            "score": r["score"], "pieces_json": r["pieces_json"], "purpose": rs["purpose"],
                            "temperature": rs["temperature"], "humidity": rs["humidity"]})
        return out
    if name == "disliked_outfit_rows":
        return [{"id": f["id"], "user_id": uid, "pieces_json": recs[f["recommendation_id"]]["pieces_json"]}
                for f in mine["feedback"] if f.get("rating") == "dislike" and f["recommendation_id"] in recs
                and recs[f["recommendation_id"]]["session_id"] in sessions]
    if name == "recommendation_log_rows":
        fb = {f["recommendation_id"]: f for f in mine["feedback"]}
        out = []
        for rs in sessions.values():
            for r in [r for r in recs.values() if r["session_id"] == rs["id"]] or [None]:
                f = fb.get(r["id"]) if r else None
                out.append({"user_id": uid, "session_id": rs["id"],
                            **{k: rs[k] for k in ("started_at", "completed_at", "purpose", "temperature",
                                                  "apparent_temperature", "humidity", "precipitation", "rain",
                                                  "wind_speed", "weather_source")},
                            "recommendation_id": r and r["id"], "rank": r and r["rank"], "score": r and r["score"],
                            "pieces_json": r and r["pieces_json"],
                            **{k: f and f[k] for k in ("rating", "worn", "thermal_feedback", "satisfaction")}})
        return out
    if name == "kpi_rows":
        accepted = {recs[s["recommendation_id"]]["session_id"] for s in mine["saved_outfits"]
                    if s["recommendation_id"] in recs and recs[s["recommendation_id"]]["session_id"] in sessions}
        return [{"sessions": len(sessions), "accepted_sessions": len(accepted)}]
    raise KeyError(name)


VIEWS = {"saved_outfit_rows", "disliked_outfit_rows", "recommendation_log_rows", "kpi_rows"}


def _sort_key(col):
    """PostgreSQL 정렬처럼: 오름차순은 NULL 이 마지막, 내림차순(reverse)은 NULL 이 처음."""
    return lambda r: (r.get(col) is None, r.get(col) if r.get(col) is not None else 0)


def _fault(backend, key):
    """시험용 실패 주입: backend.faults[key] = "error"(저장 전 실패) / "lost"(저장은 됐는데 응답 유실) / "partial"(첫 행 뒤 실패),
    또는 [None, "error"] 처럼 부를 때마다 하나씩. key = (표, 동작) 또는 ("storage", "upload")."""
    v = backend.faults.get(key)
    if isinstance(v, list):
        return v.pop(0) if v else None
    return backend.faults.pop(key, None)


class FakeBackend:
    """Supabase 프로젝트 하나 (여러 클라이언트가 공유)."""

    def __init__(self, autoconfirm=True):
        self.tables = {}
        self.next_id = {}
        self.users = {}       # email -> {"id", "password"}
        self.objects = {}     # "bucket/path" -> bytes
        self.autoconfirm = autoconfirm
        self.calls = []
        self.max_rows = 1000  # Supabase API 의 한 번 조회 최대 줄 수 (시험에서 작게 바꿔 나눠 받기를 확인)
        self.rpc_faults = {}  # DB 함수 이름 -> "error"(중간 실패, 되돌림) / "lost"(저장됐는데 응답 유실). 한 번만 적용
        self.faults = {}      # 표 쓰기·사진 올리기 실패 주입 (_fault 참고)
        self.after_select = []  # 시험용: 조회 한 번이 끝날 때마다 하나씩 꺼내 실행 (쪽 사이에 행 추가·삭제 흉내)
        self.reverse_returning = False  # 시험용: 넣은 행을 거꾸로 돌려줌 (응답 순서에 기대는 코드가 없는지 확인)

    def rows(self, table):
        return self.tables.setdefault(table, [])


class _Result(SimpleNamespace):
    pass


class _Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op, self.payload, self.filters = "select", None, []
        self.order_by, self.on_conflict, self.ignore_dup = [], None, False
        self.count, self.window, self.row_limit = None, None, None

    # 조건
    def select(self, cols="*", count=None, **kw):
        self.op, self.count = "select", count
        return self

    def lt(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and r.get(col) < val)
        return self

    def gt(self, col, val):
        self.filters.append(lambda r: r.get(col) is not None and r.get(col) > val)
        return self

    def limit(self, n):
        self.row_limit = n
        return self

    def range(self, start, end):
        assert self.window is None, "postgrest-py 는 range 를 두 번 걸면 인자가 겹친다"
        self.window = (start, end)
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
        self.order_by.append((col, desc))
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
        if self.table in VIEWS:
            return _view_rows(self.client.backend, uid, self.table)
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
        fault = None if self.op == "select" else _fault(b, (self.table, self.op))
        self.partial_fault = fault == "partial"
        if fault == "error":
            raise FakeAPIError(f"server error while writing {self.table}")
        before = None if self.op == "select" else copy.deepcopy((b.tables, b.next_id))
        try:
            result = self._apply(b, uid, rows)
        except Exception:                 # 요청 하나 = 문장 하나 = 거래 하나: 중간에 실패하면 아무것도 안 바뀜
            if before is not None:
                b.tables, b.next_id = before
            raise
        if fault == "lost":
            raise FakeAPIError(f"connection lost after writing {self.table}")
        return result

    def _apply(self, b, uid, rows):
        if self.op == "select":
            out = copy.deepcopy(rows)
            for col, desc in reversed(self.order_by):          # 뒤 정렬부터 (안정 정렬로 여러 열 정렬)
                out.sort(key=_sort_key(col), reverse=desc)
            total = len(out)
            if self.window:
                if self.count == "exact" and self.window[0] > 0 and self.window[0] >= total:
                    raise FakeAPIError("PGRST103 Requested range not satisfiable (416)")   # PostgREST 흉내 (근사)
                out = out[self.window[0]:self.window[1] + 1]
            if self.row_limit is not None:
                out = out[:self.row_limit]
            result = _Result(data=out[:b.max_rows], count=total if self.count == "exact" else None)
            if b.after_select and self.table != "profiles":
                b.after_select.pop(0)()
            return result
        if self.op == "update":
            for r in rows:
                r.update(copy.deepcopy(self.payload))
            return _Result(data=copy.deepcopy(rows))
        if self.op == "delete":                       # RLS: 내 행 중 조건에 맞는 것만 지워진다
            gone = [id(r) for r in rows]
            b.tables[self.table] = [r for r in b.rows(self.table) if id(r) not in gone]
            return _Result(data=copy.deepcopy(rows))
        payload = self.payload if isinstance(self.payload, list) else [self.payload]
        created, touched = [], set()
        for row in payload:
            row = copy.deepcopy(row)
            self._check_row(row, uid)
            if self.table in UNIQUE and all(row.get(k) is not None for k in UNIQUE[self.table]):
                keys = UNIQUE[self.table]
                same = next((r for r in b.rows(self.table) if all(r.get(k) == row.get(k) for k in keys)), None)
                if same is not None:
                    if self.op == "upsert" and self.ignore_dup:      # on conflict do nothing
                        continue
                    if self.op == "upsert":                          # on conflict do update (merge-duplicates)
                        if id(same) in touched:
                            raise FakeAPIError("ON CONFLICT DO UPDATE command cannot affect row a second time")
                        touched.add(id(same))
                        same.update(row)
                        created.append(copy.deepcopy(same))
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
            touched.add(id(row))
            created.append(copy.deepcopy(row))
            if self.partial_fault:                   # 시험용: 첫 행을 넣은 뒤 실패 (요청 전체가 되돌아가야 함)
                raise FakeAPIError(f"server error in the middle of writing {self.table}")
        return _Result(data=created[::-1] if b.reverse_returning else created)


class _RPC:
    """DB 함수(supabase/schema.sql 의 buy_candidate, save_recommendation_session) 흉내.
    함수 한 번이 거래 하나라 중간에 실패하면 표 전체를 되돌린다. 진짜 거래·RLS 확인은 tests/test_rls_sql.py."""

    PARAMS = {
        "buy_candidate": {"p_candidate_id", "p_created_at"},
        "save_recommendation_session": {"p_request_id", "p_purpose", "p_temperature", "p_apparent_temperature",
                                        "p_humidity", "p_precipitation", "p_rain", "p_wind_speed",
                                        "p_weather_source", "p_started_at", "p_results"},
        "export_user_logs": set(),
        "save_wardrobe_items": {"p_rows"},
    }

    def __init__(self, client, fn, params):
        self.client, self.fn, self.params = client, fn, dict(params or {})

    def execute(self):
        b = self.client.backend
        b.calls.append((self.fn, "rpc"))
        if self.client.uid is None:
            raise FakeAPIError("permission denied for function (not signed in)")
        if set(self.params) != self.PARAMS.get(self.fn):
            raise FakeAPIError(f"Could not find the function public.{self.fn} with these parameters")
        fault = b.rpc_faults.pop(self.fn, None)
        before, cap = copy.deepcopy((b.tables, b.next_id)), b.max_rows
        b.max_rows = 10 ** 9                 # DB 함수 안의 조회는 API 반환 한도를 받지 않는다
        try:
            data = getattr(self, "_" + self.fn)(self.params)
            if fault == "error":
                raise FakeAPIError("server error in the middle of the function")
        except Exception:
            b.tables, b.next_id = before
            raise
        finally:
            b.max_rows = cap
        if fault == "lost":
            raise FakeAPIError("connection lost after the function committed")
        return _Result(data=data)

    def _buy_candidate(self, p):
        t = self.client.table
        taken = t("purchase_candidates").update({"deleted": 1}).eq("id", p["p_candidate_id"]).eq("deleted", 0).execute().data
        if not taken:
            return []
        c = taken[0]
        new = t("wardrobe_items").insert({
            "user_id": c["user_id"], "name": c["name"], "category": c["category"], "subcategory": c["subcategory"],
            "color": c["color"], "warmth": c["warmth"], "rain_ok": c["rain_ok"], "material": "",
            "image_path": c["image_path"] or "", "notes": c["link"] or "", "ai_category": None,
            "ai_subcategory": c["ai_subcategory"], "ai_confidence": c["ai_confidence"],
            "created_at": p["p_created_at"]}).execute().data
        return [{"new_item_id": new[0]["id"]}]

    def _save_wardrobe_items(self, p):
        """옷 묶음 저장 함수: 행마다 차례로 넣거나(없으면) 조건부로 고친다(지우지 않았고 '정보 수정' 안 함). 안의 넣기·고치기는
        표 요청을 그대로 써서 시험의 실패 주입(faults)도 함수 안에서 걸리고, 실패하면 함수 전체가 되돌아간다."""
        t, out = self.client.table, []
        for r in p["p_rows"]:
            old = t("wardrobe_items").select("*").eq("request_id", r["request_id"]).execute().data
            if not old:
                new = t("wardrobe_items").insert({**r, "user_id": self.client.uid}).execute().data[0]
                out.append({"req": r["request_id"], "item_id": new["id"], "kept_as_is": False})
            elif old[0]["deleted"] == 0 and old[0]["edited_at"] is None:
                t("wardrobe_items").update({k: v for k, v in r.items() if k != "request_id"}).eq("id", old[0]["id"]).execute()
                out.append({"req": r["request_id"], "item_id": old[0]["id"], "kept_as_is": False})
            else:
                out.append({"req": r["request_id"], "item_id": old[0]["id"], "kept_as_is": True})
        return out

    def _export_user_logs(self, p):
        """jsonb 하나(목록)를 돌려주는 함수: PostgREST 는 그 값을 그대로 data 로 준다 (반환 줄 한도 없음)."""
        rows = _view_rows(self.client.backend, self.client.uid, "recommendation_log_rows")
        rows.sort(key=lambda r: (r["recommendation_id"] is not None, r["recommendation_id"] or 0))
        rows.sort(key=lambda r: (r["rank"] is not None, r["rank"] or 0))
        rows.sort(key=lambda r: r["session_id"], reverse=True)
        out = []
        for r in rows:
            d = {k: (int(v) if isinstance(v, float) and v.is_integer() else v)    # jsonb 처럼 22.0 → 22
                 for k, v in r.items() if k not in ("user_id", "pieces_json")}
            d["outfit"] = None if r["pieces_json"] is None else " + ".join(x["name"] for x in json.loads(r["pieces_json"]))
            out.append(d)
        return out

    def _save_recommendation_session(self, p):
        t, uid = self.client.table, self.client.uid
        old = t("recommendation_sessions").select("*").eq("request_id", p["p_request_id"]).execute().data
        if old:
            sid = old[0]["id"]
        else:
            sid = t("recommendation_sessions").insert({
                "user_id": uid, "request_id": p["p_request_id"], "purpose": p["p_purpose"],
                "temperature": p["p_temperature"], "apparent_temperature": p["p_apparent_temperature"],
                "humidity": p["p_humidity"], "precipitation": p["p_precipitation"], "rain": p["p_rain"],
                "wind_speed": p["p_wind_speed"], "weather_source": p["p_weather_source"],
                "started_at": p["p_started_at"]}).execute().data[0]["id"]
            rows = [{"user_id": uid, "session_id": sid, "rank": i, "score": r["score"], "pieces_json": r["pieces_json"],
                     "reasons_json": r["reasons_json"], "components_json": r["components_json"],
                     "created_at": p["p_started_at"]} for i, r in enumerate(p["p_results"], 1)]
            if rows:
                t("recommendations").insert(rows).execute()
        recs = t("recommendations").select("*").eq("session_id", sid).order("rank").execute().data
        return [{"new_session_id": sid, "new_recommendation_ids": [r["id"] for r in recs]}]


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
        b, key = self.client.backend, f"{self.name}/{path}"
        fault = _fault(b, ("storage", "upload"))
        if fault == "error":
            raise FakeAPIError("upload failed")
        if key in b.objects and str((file_options or {}).get("upsert", "false")).lower() != "true":
            raise FakeAPIError("The resource already exists")
        b.objects[key] = bytes(data)
        if fault == "lost":
            raise FakeAPIError("connection lost after upload")
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

    def rpc(self, fn, params=None):
        return _RPC(self, fn, params)


def signed_in_client(backend, email, password="secret123"):
    c = FakeClient(backend)
    try:
        c.auth.sign_up({"email": email, "password": password})
    except FakeAPIError:
        c.auth.sign_in_with_password({"email": email, "password": password})
    return c
