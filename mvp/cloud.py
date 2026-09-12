"""직원 세션별 Supabase 연결. publishable 키 + 사용자 JWT로 RLS를 적용합니다."""

import re
import time

import httpx
import numpy as np

from mvp.library import Chunk, Hit, chunk_payload
from mvp.settings import GuideError, reject_secret_key

OPTIONAL_CHUNK_COLUMNS = frozenset({
    'source_type', 'location', 'normalized_text', 'previous_chunk_id', 'next_chunk_id', 'parent_id',
})


def database_stage(path):
    """고정된 처리명만 표시하고 URL·ID·인증정보는 오류에 포함하지 않습니다."""
    return {
        '/rest/v1/guide_chunks': 'SOURCE', '/rest/v1/rpc/guide_search': 'SEARCH',
        '/rest/v1/guide_documents': 'DOCUMENTS', '/rest/v1/guide_checklists': 'CHECKLISTS',
    }.get(path, 'REQUEST')


class DatabaseError(GuideError):
    """응답 본문을 보관하지 않고 안전한 진단 코드와 알려진 누락 열만 추출합니다."""

    def __init__(self, response, path):
        self.status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        code = payload.get('code', '')
        self.code = code if isinstance(code, str) and re.fullmatch(r'(?:[0-9A-Z]{5}|PGRST\d{3})', code) else 'UNKNOWN'
        self.missing_column = None
        if self.status == 400 and path == '/rest/v1/guide_chunks' and self.code in {'42703', 'PGRST204'}:
            message = payload.get('message', '')
            if isinstance(message, str):
                patterns = (
                    r'column (?:(?:"?public"?\.)?"?guide_chunks(?:_\d+)?"?\.)?"?([a-z_]+)"? does not exist',
                    r"Could not find the '([a-z_]+)' column of 'guide_chunks' in the schema cache",
                )
                for pattern in patterns:
                    match = re.fullmatch(pattern, message)
                    if match and match[1] in OPTIONAL_CHUNK_COLUMNS:
                        self.missing_column = match[1]
                        break
        if self.status in {401, 403}:
            advice = '자료 조회 권한을 확인하지 못했습니다. 다시 로그인하거나 관리자에게 문의하세요.'
        elif self.code in {'PGRST202', '42883'}:
            advice = '데이터베이스 검색 함수가 없거나 버전이 맞지 않습니다. 관리자에게 문의하세요.'
        elif self.code in {'57014', 'PGRST003'}:
            advice = '데이터베이스 요청 시간이 초과되었습니다. 검색할 지침서를 줄여 다시 시도해 주세요.'
        else:
            advice = '데이터베이스 요청을 처리하지 못했습니다. 아래 오류 코드를 관리자에게 전달해 주세요.'
        super().__init__(f'{advice} (DATABASE/{database_stage(path)}/HTTP{self.status}/{self.code})')


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
        self._chunk_columns = tuple(Chunk.__dataclass_fields__)

    def request(self, method, path, **kwargs):
        headers = {"apikey": self.settings.supabase_key}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = self.client.request(method, self.settings.supabase_url.rstrip("/") + path,
                                           headers=headers, **kwargs)
            if response.status_code >= 400:
                raise DatabaseError(response, path)
            return response.json() if response.content else None
        except GuideError:
            raise
        except (httpx.HTTPError, ValueError):
            raise GuideError('Supabase 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요. '
                             f'(DATABASE/{database_stage(path)}/NETWORK)') from None

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
        self._chunk_columns = tuple(Chunk.__dataclass_fields__)

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

    def search(self, question, vector, doc_ids, minimum, plan=None, trace=None):
        from mvp.context import expand_context
        from mvp.query import plan_query
        from mvp.retrieval import BM25Index, rerank, rrf
        from mvp.search_trace import begin_trace, candidate_trace, finish_trace

        if trace is not None:
            self.require_admin()
        plan = plan or plan_query(question)
        begin_trace(trace, plan, doc_ids, minimum)
        if not doc_ids or plan.clarification or plan.domain == 'out_of_scope':
            if trace is not None:
                trace['reason'] = 'empty_scope_or_domain_or_clarification'
            return []
        self.authorize()
        allowed = set(doc_ids) & (set(plan.document_ids) if plan.document_ids else set(doc_ids))
        documents = [d for d in self.documents() if d['id'] in allowed]
        allowed = {d['id'] for d in documents}
        if trace is not None:
            trace.update(backend='Supabase pgvector RPC + local BM25', allowed_document_ids=sorted(allowed),
                         reason='no_active_documents')
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
            if trace is not None:
                trace['reason'] = 'no_authorized_chunks'
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
        hit_by_id = {h.chunk.id: h for h in candidates}
        candidate_trace(trace, chunks, scores, [hit_by_id[r['id']] for r in dense], candidates)
        if trace is not None:
            trace['vector_score_note'] = 'RPC가 반환하지 않은 BM25 전용 후보의 similarity=0은 미측정 대체값입니다.'
            trace['rpc_returned_count'] = len(rows)
            trace['rpc_excluded_ids'] = [r['id'] for r in rows if r['id'] not in by_id]
        seeds = rerank(plan, candidates, minimum, trace=trace)
        hits = expand_context(question, seeds, chunks, limit=plan.max_hits)
        finish_trace(trace, seeds, hits)
        if trace is not None:
            self.require_admin()
        return hits

    def diagnostic_vectors(self, doc_id):
        """관리자가 요청한 문서의 실제 저장 벡터만 JWT/RLS로 읽습니다."""
        self.require_admin()
        result = []
        for offset in range(0, 5000, 500):
            rows = self.request('GET', '/rest/v1/guide_chunks', params={
                'select': 'id,embedding', 'document_id': 'eq.' + doc_id,
                'order': 'index', 'offset': str(offset), 'limit': '500',
            })
            result.extend((row['id'], row.get('embedding')) for row in rows)
            if len(rows) < 500:
                break
        self.require_admin()
        return result

    def reindex(self, metadata, chunks, vectors):
        self.require_admin()
        rows = [{**chunk_payload(c), "embedding": vector.tolist()}
                for c, vector in zip(chunks, vectors, strict=True)]
        self.request("POST", "/rest/v1/rpc/guide_reindex",
                     json={"doc": metadata, "parts": rows})
    def _source_rows(self, params):
        # 구버전/부분 마이그레이션 DB도 지원합니다. 서버가 없다고 확인한 선택 열만 제외하며,
        # 문서명·페이지·본문 등 필수 열과 존재하는 문맥 메타데이터는 그대로 유지합니다.
        columns = self._chunk_columns
        for _ in range(len(OPTIONAL_CHUNK_COLUMNS) + 1):
            try:
                rows = self.request('GET', '/rest/v1/guide_chunks',
                                    params={**params, 'select': ','.join(columns)})
            except DatabaseError as error:
                if error.missing_column not in columns:
                    raise
                columns = tuple(c for c in columns if c != error.missing_column)
            else:
                self._chunk_columns = columns
                return rows
        raise GuideError('지침서 원문 열을 확인하지 못했습니다. 관리자에게 문의하세요. (DATABASE/SOURCE/SCHEMA)')

    def source_chunks(self, doc_id, chunk_ids=None):
        self.authorize()
        # 벡터는 pgvector에서 검색합니다. BM25/출처 조회에 384차원 벡터를 전송하지 않습니다.
        if chunk_ids is not None:
            if not chunk_ids:
                return []
            rows = self._source_rows({
                "document_id": "eq." + doc_id, "id": "in.(" + ",".join(sorted(set(chunk_ids))) + ")",
                "order": "index", "limit": "40",
            })
            return [Chunk.from_row(row) for row in rows]
        result = []
        for offset in range(0, 5000, 500):
            rows = self._source_rows({
                "document_id": "eq." + doc_id,
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
