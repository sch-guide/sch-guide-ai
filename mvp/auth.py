"""이 PC용 로그인. 직원 배포에서는 cloud.StaffLibrary의 Supabase Auth를 사용합니다."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from mvp.settings import GuideError


class LocalAuth:
    def __init__(self, directory):
        self.path = Path(directory) / "accounts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token = self.user_id = ""
        with self.db() as db:
            db.executescript("""
                create table if not exists users(
                    id text primary key, username text unique not null,
                    salt text not null, password_hash text not null,
                    role text not null check(role in ('admin','staff')), active integer not null default 1);
                create table if not exists sessions(
                    token_hash text primary key, user_id text not null, expires real not null);
                create table if not exists attempts(username text, at real);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(password, salt):
        return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    @staticmethod
    def credentials(username, password):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,79}", username):
            raise GuideError("아이디는 영문·숫자 등으로 3~80자 입력하세요.")
        if not 10 <= len(password) <= 200:
            raise GuideError("비밀번호는 10~200자로 입력하세요.")
        return username

    def needs_setup(self):
        with self.db() as db:
            return db.execute("select count(*) from users").fetchone()[0] == 0

    def bootstrap(self, username, password):
        """앱의 localhost 전용 화면에서 최초 한 번만 호출합니다."""
        username = self.credentials(username, password)
        with self.db() as db:
            db.execute("begin immediate")
            if db.execute("select count(*) from users").fetchone()[0]:
                raise GuideError("관리자 계정이 이미 있습니다. 로그인하세요.")
            self._insert(db, username, password, "admin")
        return self.login(username, password)

    def _insert(self, db, username, password, role):
        salt = secrets.token_hex(16)
        try:
            db.execute("insert into users values(?,?,?,?,?,1)",
                       (str(uuid4()), username, salt, self.digest(password, salt), role))
        except sqlite3.IntegrityError:
            raise GuideError("이미 등록된 아이디입니다.") from None

    def login(self, username, password):
        self.token = self.user_id = ""
        username = username.strip().lower()[:80]
        if len(password) > 200:
            raise GuideError("로그인 정보를 확인하세요. (LOGIN)")
        now = time.time()
        with self.db() as db:
            db.execute("begin immediate")
            db.execute("delete from attempts where at < ?", (now - 900,))
            if db.execute("select count(*) from attempts where username=?", (username,)).fetchone()[0] >= 5:
                raise GuideError("로그인 시도가 많습니다. 15분 뒤 다시 시도하세요. (LOGIN_LIMIT)")
            row = db.execute("select * from users where username=?", (username,)).fetchone()
            computed = self.digest(password, row["salt"] if row else "00" * 16)
            valid = bool(row and row["active"] and hmac.compare_digest(computed, row["password_hash"]))
            if not valid:
                db.execute("insert into attempts values(?,?)", (username, now))
            else:
                db.execute("delete from attempts where username=?", (username,))
                db.execute("delete from sessions where expires < ?", (now,))
                self.token = secrets.token_urlsafe(32)
                db.execute("insert into sessions values(?,?,?)",
                           (self.token_hash(), row["id"], now + 8 * 3600))
        if not valid:
            raise GuideError("아이디·비밀번호 또는 직원 등록 상태를 확인하세요. (LOGIN)")
        return self.authorize()

    def token_hash(self):
        return hashlib.sha256(self.token.encode()).hexdigest()

    def authorize(self):
        with self.db() as db:
            row = db.execute("""select u.id as user_id,u.username,u.role from users u
                join sessions s on s.user_id=u.id
                where s.token_hash=? and s.expires>? and u.active=1""",
                (self.token_hash(), time.time())).fetchone() if self.token else None
        if not row:
            self.token = self.user_id = ""
            raise GuideError("로그인이 만료되었거나 직원 권한이 없습니다. 다시 로그인하세요. (ACCESS)")
        self.user_id = row["user_id"]
        return dict(row)

    def require_admin(self):
        if self.authorize()["role"] != "admin":
            raise GuideError("관리자만 지침서와 계정을 변경할 수 있습니다. (ADMIN_REQUIRED)")

    def logout(self):
        with self.db() as db:
            db.execute("delete from sessions where token_hash=?", (self.token_hash(),))
        self.token = self.user_id = ""

    def create_user(self, username, password, role="staff"):
        self.require_admin()
        username = self.credentials(username, password)
        if role not in {"staff", "admin"}:
            raise GuideError("계정 역할이 잘못되었습니다.")
        with self.db() as db:
            self._insert(db, username, password, role)

    def users(self):
        self.require_admin()
        with self.db() as db:
            return [dict(r) for r in db.execute("select id,username,role,active from users order by username")]

    def deactivate(self, user_id):
        self.require_admin()
        if user_id == self.user_id:
            raise GuideError("현재 로그인한 관리자 계정은 비활성화할 수 없습니다.")
        with self.db() as db:
            db.execute("update users set active=0 where id=?", (user_id,))
            db.execute("delete from sessions where user_id=?", (user_id,))
