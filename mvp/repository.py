"""공용 지침서 저장소: SQLite 카탈로그 + 서버 FAISS 캐시 + 분리된 원본 저장소.

이 파일에는 LLM 호출이 없습니다. 문서/벡터는 Streamlit 사용자 세션에 넣지 않습니다.
"""

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid4

import numpy as np

from mvp.documents import PdfInputError, read_document
from mvp.library import Chunk, LocalLibrary, chunk_payload, make_chunks, protect_private, validate_checklist
from mvp.settings import DIMENSIONS, MODEL, GuideError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dump(value):
    return json.dumps(value, ensure_ascii=False)


@lru_cache(maxsize=8)
def server_lock(path):
    return threading.RLock()


@lru_cache(maxsize=8)
def search_lock(path):
    # 긴 문서 색인 작업이 진행 중이어도 직원은 기존 색인을 검색할 수 있습니다.
    return threading.RLock()


@contextmanager
def database(path):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


@lru_cache(maxsize=2)
def snapshot(path, revision):
    """승인된 공용 지침만 캐시합니다. 질문·원본 파일·직원 정보는 캐시하지 않습니다."""
    result = LocalLibrary()
    with database(path) as db:
        db.execute("begin")
        current = db.execute("select version from corpus").fetchone()[0]
        if current != revision:
            raise GuideError("지침서가 변경되었습니다. 다시 검색하세요. (CORPUS_CHANGED)")
        result.docs = [json.loads(r[0]) for r in db.execute(
            "select metadata from documents where status='ready' order by created_at,id")]
        if any(d["model"] != MODEL for d in result.docs):
            raise GuideError("검색 모델이 변경되었습니다. 관리자가 전체 문서를 재색인해야 합니다. (INDEX_MODEL)")
        rows = db.execute("""select c.payload,c.vector from chunks c join documents d on d.id=c.document_id
            where d.status='ready' order by d.created_at,d.id,c.position""").fetchall()
    result.chunks = [Chunk.from_row(json.loads(r[0])) for r in rows]
    result.vectors = (np.stack([np.frombuffer(r[1], dtype="<f4") for r in rows])
                      if rows else np.empty((0, DIMENSIONS), dtype=np.float32))
    return result


class Repository:
    def __init__(self, settings, auth, store):
        self.settings, self.auth, self.store = settings, auth, store
        settings.library_dir.mkdir(parents=True, exist_ok=True)
        self.path = str(settings.library_dir / "catalog.sqlite3")
        self.lock = server_lock(self.path)
        self.storage_tag = settings.storage_backend + ":" + (
            settings.supabase_url + "/" + settings.storage_bucket if settings.storage_backend == "supabase"
            else str(settings.library_dir / "originals"))
        with database(self.path) as db:
            db.executescript("""
                pragma journal_mode=WAL;
                create table if not exists corpus(version integer not null);
                insert into corpus select 0 where not exists(select 1 from corpus);
                create table if not exists documents(
                    id text primary key, metadata text not null, file_hash text not null,
                    source_key text not null, storage_tag text not null, status text not null,
                    created_at text not null, indexed_at text, last_error text not null default '',
                    replaces_id text);
                create table if not exists chunks(
                    id text primary key, document_id text not null, position integer not null,
                    payload text not null, vector blob not null);
                create index if not exists chunks_document on chunks(document_id);
                create table if not exists checklists(
                    id text primary key, document_id text not null, payload text not null, active integer not null);
            """)

    def authorize(self):
        return self.auth.authorize()

    def revision(self):
        self.authorize()
        with database(self.path) as db:
            return db.execute("select version from corpus").fetchone()[0]

    def ensure_revision(self, expected):
        if self.revision() != expected:
            raise GuideError("처리 중 지침서가 변경되었습니다. 다시 질문해 주세요. (CORPUS_CHANGED)")

    def documents(self, all_status=False):
        self.auth.require_admin() if all_status else self.authorize()
        with database(self.path) as db:
            rows = db.execute("select * from documents " +
                              ("" if all_status else "where status='ready' ") + "order by created_at,id").fetchall()
        return [{**json.loads(r["metadata"]), "status": r["status"], "created_at": r["created_at"],
                 "indexed_at": r["indexed_at"], "last_error": r["last_error"],
                 "replaces_id": r["replaces_id"]} for r in rows]

    def _row(self, doc_id):
        with database(self.path) as db:
            row = db.execute("select * from documents where id=?", (doc_id,)).fetchone()
        if not row:
            raise GuideError("등록된 문서를 찾을 수 없습니다.")
        return dict(row)

    def _read_original(self, row):
        self.auth.require_admin()
        if row["storage_tag"] != self.storage_tag:
            raise GuideError("등록 당시 원본 저장소와 현재 설정이 다릅니다. 관리자 설정을 확인하세요. (STORAGE_CONFIG)")
        try:
            return read_document(json.loads(row["metadata"])["document_name"], self.store.read(row["source_key"]),
                                 ocr=self.settings.ocr_enabled)
        except PdfInputError as exc:
            raise GuideError(str(exc)) from None

    def preview(self, doc_id):
        self.auth.require_admin()
        row = self._row(doc_id)
        if row["status"] not in {"ready", "pending", "error"}:
            raise GuideError("현재 등록 중인 문서만 확인할 수 있습니다.")
        return self._read_original(row)

    def register(self, name, content, embedder, *, title="", section="", updated_date=None, replaces_id=None):
        self.auth.require_admin()
        try:
            document = read_document(name, content, ocr=self.settings.ocr_enabled)
        except PdfInputError as exc:
            raise GuideError(str(exc)) from None
        protect_private("\n".join([document.document_name, title, section] + [p.text for p in document.pages]))
        if not document.text_page_count:
            raise GuideError("검색할 글이 없습니다. 스캔 문서는 OCR 후 등록하세요.")
        digest = hashlib.sha256(content).hexdigest()
        doc_id = str(uuid4())
        key = doc_id + "." + document.source_type
        metadata = dict(id=doc_id, document_name=document.document_name, title=title.strip() or document.document_name,
                        section=section.strip(), updated_date=updated_date, file_hash=digest,
                        source_type=document.source_type, page_count=len(document.pages), model=MODEL)
        with self.lock:
            self.auth.require_admin()
            if replaces_id and self._row(replaces_id)["status"] != "ready":
                raise GuideError("현재 검색 중인 지침서만 교체할 수 있습니다.")
            with database(self.path) as db:
                if db.execute("select 1 from documents where file_hash=? and status in ('ready','pending','error')",
                              (digest,)).fetchone():
                    raise GuideError("같은 파일이 이미 등록되어 있습니다. 해당 문서의 재색인을 사용하세요.")
            self.store.put(key, content)
            try:
                with database(self.path) as db:
                    db.execute("""insert into documents(id,metadata,file_hash,source_key,storage_tag,status,
                        created_at,replaces_id) values(?,?,?,?,?,'pending',?,?)""",
                        (doc_id, dump(metadata), digest, key, self.storage_tag, now(), replaces_id))
            except Exception:
                self.store.delete(key)
                raise
            # 실패하면 원본과 오류 상태를 남겨 관리자가 재시도할 수 있습니다.
            self.reindex(doc_id, embedder)
        return doc_id

    def reindex(self, doc_id, embedder):
        self.auth.require_admin()
        with self.lock:
            row = self._row(doc_id)
            if row["status"] not in {"pending", "error", "ready"}:
                raise GuideError("삭제·교체된 문서는 재색인할 수 없습니다.")
            try:
                document = self._read_original(row)
                old = json.loads(row["metadata"])
                metadata, parts = make_chunks(document, embedder, title=old["title"], section=old["section"],
                    updated_date=old["updated_date"], file_hash=row["file_hash"])
                metadata["id"] = doc_id
                parts = [replace(c, document_id=doc_id) for c in parts]
                # 현재 문서/교체 대상에 이미 저장된 동일 원문 벡터만 재사용합니다.
                reusable = {}
                with database(self.path) as db:
                    stored = db.execute('select c.payload,c.vector,d.metadata from chunks c join documents d on d.id=c.document_id '
                                        'where c.document_id in (?,?)', (doc_id, row['replaces_id'] or doc_id)).fetchall()
                for entry in stored:
                    if json.loads(entry['metadata']).get('model') == MODEL:
                        reusable[json.loads(entry['payload'])['text']] = np.frombuffer(entry['vector'], dtype='<f4')
                missing = list(dict.fromkeys(c.text for c in parts if c.text not in reusable))
                if missing:
                    computed = np.asarray(embedder.encode(missing), dtype='<f4')
                    if computed.shape != (len(missing), DIMENSIONS) or not np.isfinite(computed).all():
                        raise GuideError('검색 벡터 형식이 잘못되었습니다. (INDEX)')
                    reusable.update(zip(missing, computed, strict=True))
                vectors = np.stack([reusable[c.text] for c in parts]).astype('<f4')
                metadata['embedding_computed'] = len(missing)
                metadata['embedding_reused'] = len(parts) - len(missing)
                if vectors.shape != (len(parts), DIMENSIONS) or not np.isfinite(vectors).all():
                    raise GuideError("검색 벡터 형식이 잘못되었습니다. (INDEX)")
                self.auth.require_admin()
                with database(self.path) as db:
                    db.execute("begin immediate")
                    current = db.execute("select status,indexed_at from documents where id=?", (doc_id,)).fetchone()
                    if current["status"] != row["status"] or current["indexed_at"] != row["indexed_at"]:
                        raise GuideError("다른 작업에서 문서를 변경했습니다. 목록을 새로 확인하세요.")
                    replaced = row["replaces_id"]
                    # 교체본의 이후 재색인에서는 이미 교체된 문서를 다시 변경하지 않습니다.
                    if replaced and row["status"] != "ready":
                        result = db.execute("update documents set status='replaced' where id=? and status='ready'", (replaced,))
                        if result.rowcount != 1:
                            raise GuideError("교체 대상이 이미 변경되었습니다. 새 지침서로 등록하세요.")
                        db.execute("delete from chunks where document_id=?", (replaced,))
                        db.execute("update checklists set active=0 where document_id=?", (replaced,))
                    count = db.execute("select count(*) from chunks where document_id<>?", (doc_id,)).fetchone()[0]
                    if count + len(parts) > 20000:
                        raise GuideError("현재 서버는 총 20,000개 문단까지 지원합니다. 구버전 문서를 정리하세요.")
                    db.execute("delete from chunks where document_id=?", (doc_id,))
                    db.executemany("insert into chunks values(?,?,?,?,?)",
                        [(c.id, doc_id, c.index, dump(chunk_payload(c)), v.tobytes()) for c, v in zip(parts, vectors, strict=True)])
                    db.execute("update documents set metadata=?,status='ready',indexed_at=?,last_error='' where id=?",
                               (dump(metadata), now(), doc_id))
                    # 원문 ID가 달라지면 기존 체크리스트는 재검토 전까지 숨깁니다.
                    db.execute("update checklists set active=0 where document_id=?", (doc_id,))
                    db.execute("update corpus set version=version+1")
            except Exception as exc:
                message = str(exc) if isinstance(exc, GuideError) else "검색 색인 생성에 실패했습니다. 원본·메모리·저장소를 확인하세요. (INDEX)"
                with database(self.path) as db:
                    db.execute("""update documents set status=case when status='ready' then 'ready' else 'error' end,
                        last_error=? where id=? and status in ('pending','error','ready')""", (message, doc_id))
                raise GuideError(message) from None

    def retire(self, doc_id):
        self.auth.require_admin()
        with self.lock:
            row = self._row(doc_id)
            if row["storage_tag"] != self.storage_tag:
                raise GuideError("등록 당시 저장소 설정으로 되돌린 뒤 삭제하세요. (STORAGE_CONFIG)")
            # 먼저 검색·체크리스트를 제외합니다. 원본 삭제 실패 시 직원에게 다시 노출하지 않습니다.
            with database(self.path) as db:
                db.execute("update documents set status='deleted' where id=?", (doc_id,))
                db.execute("delete from chunks where document_id=?", (doc_id,))
                db.execute("delete from checklists where document_id=?", (doc_id,))
                db.execute("update corpus set version=version+1")
            try:
                self.store.delete(row["source_key"])
            except GuideError as exc:
                with database(self.path) as db:
                    db.execute("update documents set last_error=? where id=?", (str(exc), doc_id))
                raise
            with database(self.path) as db:
                db.execute("update documents set last_error='' where id=?", (doc_id,))

    def search(self, question, vector, doc_ids, minimum, plan=None):
        self.authorize()
        protect_private(question)
        revision = self.revision()
        # 공유 FAISS 인덱스 생성과 검색을 보호합니다. LLM 호출 중에는 잠그지 않습니다.
        with search_lock(self.path):
            library = snapshot(self.path, revision)
            allowed = {d["id"] for d in library.docs}
            hits = library.search(question, vector, [d for d in doc_ids if d in allowed], minimum, plan)
        self.ensure_revision(revision)
        return hits

    def source_chunks(self, doc_id, chunk_ids=None):
        self.authorize()
        if chunk_ids is None:
            self.auth.require_admin()
        elif len(chunk_ids) > 40:
            raise GuideError("한 번에 확인할 출처가 너무 많습니다.")
        with database(self.path) as db:
            rows = db.execute("""select c.payload from chunks c join documents d on d.id=c.document_id
                where d.id=? and d.status='ready' order by c.position""", (doc_id,)).fetchall()
        return [c for r in rows if (c := Chunk.from_row(json.loads(r[0]))) and
                (chunk_ids is None or c.id in chunk_ids)]

    def source_context(self, chunk, expected_revision):
        """직원에게는 해당 근거 주변 최대 5개 문단만 제공합니다. 원본 접근은 없습니다."""
        from mvp.context import neighbors
        self.ensure_revision(expected_revision)
        with search_lock(self.path):
            library = snapshot(self.path, expected_revision)
            current = next((c for c in library.chunks if c.id == chunk.id
                            and c.document_id == chunk.document_id), None)
            result = neighbors(current, library.chunks) if current else []
        self.ensure_revision(expected_revision)
        return result

    def list_checklists(self, doc_ids):
        self.authorize()
        with database(self.path) as db:
            rows = db.execute("""select c.payload from checklists c join documents d on d.id=c.document_id
                where c.active=1 and d.status='ready'""").fetchall()
        return [c for r in rows if (c := json.loads(r[0]))["document_id"] in doc_ids]

    def save_checklist(self, entry):
        self.auth.require_admin()
        with self.lock:
            chunks = self.source_chunks(entry["document_id"])
            validated = validate_checklist(entry["title"], entry["keywords"], entry["items"], chunks)
            self.auth.require_admin()
            with database(self.path) as db:
                db.execute("insert into checklists values(?,?,?,1)",
                           (validated["id"], validated["document_id"], dump(validated)))
                db.execute("update corpus set version=version+1")
        return validated
