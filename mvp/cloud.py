"""직원 세션별 Supabase 연결. publishable 키 + 사용자 JWT로 RLS를 적용합니다."""

import time

import httpx
import numpy as np

from mvp.library import Chunk, Hit, chunk_payload
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
        self._search_cache = None

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
        self._search_cache = None

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
        from mvp.context import expand_context
        from mvp.query import plan_query
        from mvp.retrieval import BM25Index, rerank, rrf

        plan = plan or plan_query(question)
        if not doc_ids or plan.clarification or plan.domain == 'out_of_scope':
            return []
        self.authorize()
        allowed = set(doc_ids) & (set(plan.document_ids) if plan.document_ids else set(doc_ids))
        documents = [d for d in self.documents() if d['id'] in allowed]
        allowed = {d['id'] for d in documents}
        if not allowed:
            return []
        # 캐시는 직원 세션에만 유지합니다. 매 검색마다 권한·문서 버전을 재확인합니다.
        key = (self.user_id, tuple(sorted((d['id'], d.get('file_hash', ''),
               d.get('indexed_at') or d.get('created_at', ''), d.get('updated_date') or '') for d in documents)))
        if self._search_cache is None or self._search_cache[0] != key:
            self._search_cache = None
            chunks = [c for doc_id in sorted(allowed) for c in self.source_chunks(doc_id)]
            self._search_cache = key, chunks, BM25Index(chunks)
        _, chunks, bm25 = self._search_cache
        if not chunks:
            return []
        # DB에서 dense top 40을, 권한 있는 전체 원문 인덱스에서 BM25 top 40을 구합니다.
        # query_terms=[]는 기존 RPC의 단순 부분문자열 순위를 사용하지 않도록 합니다.
        rows = self.request("POST", "/rest/v1/rpc/guide_search", json={
            "query_embedding": vector.tolist(), "query_terms": [],
            "document_ids": sorted(allowed), "match_count": 80,
        })
        by_id = {c.id: c for c in chunks}
        dense = sorted((r for r in rows if r['id'] in by_id), key=lambda r: (-r['similarity'], r['id']))[:40]
        scores = bm25.scores(plan.expanded)
        lexical = [int(i) for i in np.argsort(-scores, kind='stable')[:40] if scores[i] > 0]
        fusion = rrf([r['id'] for r in dense], [chunks[i].id for i in lexical])
        similarities = {r['id']: float(r['similarity']) for r in dense}
        lexical_scores = {chunks[i].id: float(scores[i]) for i in lexical}
        candidates = [Hit(by_id[identifier], similarities.get(identifier, 0),
                          bm25_score=lexical_scores.get(identifier, 0), fusion_score=value)
                      for identifier, value in fusion.items()]
        seeds = rerank(plan, candidates, minimum)
        return expand_context(question, seeds, chunks, limit=plan.max_hits)

    def reindex(self, metadata, chunks, vectors):
        self.require_admin()
        rows = [{**chunk_payload(c), "embedding": vector.tolist()}
                for c, vector in zip(chunks, vectors, strict=True)]
        self.request("POST", "/rest/v1/rpc/guide_reindex",
                     json={"doc": metadata, "parts": rows})
    def source_chunks(self, doc_id, chunk_ids=None):
        self.authorize()
        # 벡터는 pgvector에서 검색합니다. BM25/출처 조회에 384차원 벡터를 전송하지 않습니다.
        columns = ','.join(Chunk.__dataclass_fields__)
        if chunk_ids is not None:
            if not chunk_ids:
                return []
            rows = self.request("GET", "/rest/v1/guide_chunks", params={
                "document_id": "eq." + doc_id, "id": "in.(" + ",".join(sorted(set(chunk_ids))) + ")",
                "select": columns,
                "order": "index", "limit": "40",
            })
            return [Chunk.from_row(row) for row in rows]
        result = []
        for offset in range(0, 5000, 500):
            rows = self.request("GET", "/rest/v1/guide_chunks", params={
                "document_id": "eq." + doc_id,
                "select": columns,
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
