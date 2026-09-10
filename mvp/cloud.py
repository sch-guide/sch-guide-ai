"""직원 세션별 Supabase 연결. publishable 키 + 사용자 JWT로 RLS를 적용합니다."""

import time

import httpx

from mvp.library import Chunk, Hit, chunk_payload, rank_hits, terms
from mvp.settings import GuideError, reject_secret_key


class StaffLibrary:
    def __init__(self, settings, transport=None):
        reject_secret_key(settings.supabase_key)
        self.settings = settings
        self.client = httpx.Client(timeout=40, transport=transport, follow_redirects=False)
        self.token = self.refresh = ""
        self.expires = 0
        self.profile = None
        self.user_id = ""

    def request(self, method, path, **kwargs):
        headers = {"apikey": self.settings.supabase_key}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = self.client.request(method, self.settings.supabase_url.rstrip("/") + path,
                                           headers=headers, **kwargs)
            if response.status_code >= 400:
                raise GuideError("계정 권한·연결 설정·SQL 설치 상태를 확인해 주세요. (DATABASE)")
            return response.json() if response.content else None
        except GuideError:
            raise
        except (httpx.HTTPError, ValueError):
            raise GuideError("Supabase에 연결하지 못했습니다. 네트워크 상태를 확인해 주세요. (DATABASE)") from None

    def use_token(self, data):
        self.token = data["access_token"]
        self.refresh = data["refresh_token"]
        self.expires = time.time() + data.get("expires_in", 3600)

    def login(self, email, password):
        try:
            result = self.request("POST", "/auth/v1/token?grant_type=password",
                                  json={"email": email, "password": password})
            self.use_token(result)
            return self.authorize()
        except (GuideError, KeyError, TypeError):
            self.clear()
            raise GuideError("로그인하지 못했습니다. 계정·비밀번호 또는 직원 등록 상태를 확인해 주세요. (LOGIN)") from None

    def authorize(self):
        try:
            if not self.token:
                raise GuideError("로그인이 필요합니다.")
            if self.expires < time.time() + 60:
                self.use_token(self.request("POST", "/auth/v1/token?grant_type=refresh_token",
                                            json={"refresh_token": self.refresh}))
            user = self.request("GET", "/auth/v1/user")
            rows = self.request("GET", "/rest/v1/guide_profiles",
                                params={"user_id": "eq." + user["id"], "select": "user_id,role,active"})
            if not rows or not rows[0]["active"] or rows[0]["role"] not in {"admin", "staff"}:
                raise GuideError("직원 사용 권한이 없습니다.")
            self.profile, self.user_id = rows[0], user["id"]
            return self.profile
        except (GuideError, KeyError, TypeError):
            self.clear()
            raise GuideError("직원 권한을 확인하지 못했습니다. 다시 로그인하거나 관리자에게 문의하세요. (ACCESS)") from None

    def clear(self):
        self.token = self.refresh = self.user_id = ""
        self.profile = None
        self.expires = 0

    def logout(self):
        try:
            if self.token:
                self.request("POST", "/auth/v1/logout")
        finally:
            self.clear()
            self.client.close()

    def require_admin(self):
        if self.authorize()["role"] != "admin":
            raise GuideError("관리자만 지침서 자료를 변경할 수 있습니다.")

    def documents(self, all_status=False):
        self.require_admin() if all_status else self.authorize()
        params = {"select": "*", "order": "title", "limit": "500"}
        if not all_status:
            params["status"] = "eq.active"
        return self.request("GET", "/rest/v1/guide_documents", params=params)

    def publish(self, metadata, chunks, vectors):
        self.require_admin()
        rows = [{**chunk_payload(c), "embedding": vector.tolist()} for c, vector in zip(chunks, vectors, strict=True)]
        self.request("POST", "/rest/v1/rpc/guide_publish", json={"doc": metadata, "parts": rows})

    def search(self, question, vector, doc_ids, minimum, plan=None):
        if not doc_ids:
            return []
        self.authorize()
        rows = self.request("POST", "/rest/v1/rpc/guide_search", json={
            "query_embedding": vector.tolist(), "query_terms": terms(question),
            "document_ids": doc_ids, "match_count": 80,
        })
        return rank_hits(question, [Hit(Chunk.from_row(r), r["similarity"]) for r in rows], minimum)

    def reindex(self, metadata, chunks, vectors):
        self.require_admin()
        rows = [{**chunk_payload(c), "embedding": vector.tolist()}
                for c, vector in zip(chunks, vectors, strict=True)]
        self.request("POST", "/rest/v1/rpc/guide_reindex",
                     json={"doc": metadata, "parts": rows})
    def source_chunks(self, doc_id, chunk_ids=None):
        self.authorize()
        if chunk_ids is not None:
            if not chunk_ids:
                return []
            rows = self.request("GET", "/rest/v1/guide_chunks", params={
                "document_id": "eq." + doc_id, "id": "in.(" + ",".join(sorted(set(chunk_ids))) + ")",
                "select": "*",
                "order": "index", "limit": "40",
            })
            return [Chunk.from_row(row) for row in rows]
        result = []
        for offset in range(0, 5000, 500):
            rows = self.request("GET", "/rest/v1/guide_chunks", params={
                "document_id": "eq." + doc_id,
                "select": "*",
                "order": "index", "offset": str(offset), "limit": "500",
            })
            result.extend(Chunk.from_row(row) for row in rows)
            if len(rows) < 500:
                break
        return result

    def save_checklist(self, checklist):
        self.require_admin()
        self.request("POST", "/rest/v1/rpc/guide_save_checklist", json={"entry": checklist})

    def list_checklists(self, doc_ids):
        if not doc_ids:
            return []
        return self.request("GET", "/rest/v1/guide_checklists", params={
            "document_id": "in.(" + ",".join(doc_ids) + ")", "select": "*", "order": "created_at.desc",
        })

    def request_review(self, document_ids, chunk_ids):
        self.authorize()
        return self.request("POST", "/rest/v1/rpc/guide_request_review", json={
            "document_ids": list(document_ids), "chunk_ids": list(chunk_ids)
        })
    def retire(self, doc_id):
        self.require_admin()
        self.request("PATCH", "/rest/v1/guide_documents",
                     params={"id": "eq." + doc_id}, json={"status": "retired"})
