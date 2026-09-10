"""Supabase pgvector를 사용하는 직원 운영용 지침서 저장소."""

import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

from mvp.context import neighbors
from mvp.documents import PdfInputError, read_document
from mvp.library import make_chunks, protect_private, validate_checklist
from mvp.settings import DIMENSIONS, GuideError


def _revision(documents):
    rows = [(d.get('id'), d.get('status'), d.get('file_hash'), d.get('indexed_at'),
             d.get('updated_date')) for d in documents]
    return hashlib.sha256(repr(sorted(rows)).encode()).hexdigest()


class CloudRepository:
    """모든 문서 metadata와 chunk/embedding은 사용자 JWT + RLS로 접근합니다."""

    def __init__(self, settings, auth, store):
        self.settings, self.auth, self.store = settings, auth, store

    def authorize(self):
        return self.auth.authorize()

    def documents(self, all_status=False):
        rows = self.auth.documents(all_status=all_status)
        result = []
        for row in rows:
            item = dict(row)
            item['status'] = 'ready' if item.get('status') == 'active' else item.get('status', 'error')
            item.setdefault('indexed_at', item.get('created_at'))
            item.setdefault('last_error', '')
            item.setdefault('source_type', 'pdf')
            item.setdefault('chunk_count', 0)
            result.append(item)
        return result

    def revision(self):
        return _revision(self.documents())

    def ensure_revision(self, expected):
        if self.revision() != expected:
            raise GuideError('처리 중 지침서가 변경되었습니다. 다시 질문해 주세요. (CORPUS_CHANGED)')

    def _document(self, doc_id, all_status=True):
        row = next((d for d in self.documents(all_status=all_status) if d['id'] == doc_id), None)
        if not row:
            raise GuideError('등록된 문서를 찾을 수 없습니다.')
        return row

    @staticmethod
    def _key(document):
        return document['id'] + '.' + document.get('source_type', 'pdf')

    def preview(self, doc_id):
        self.auth.require_admin()
        doc = self._document(doc_id)
        try:
            return read_document(doc['document_name'], self.store.read(self._key(doc)),
                                 ocr=self.settings.ocr_enabled)
        except PdfInputError as exc:
            raise GuideError(str(exc)) from None

    def register(self, name, content, embedder, *, title='', section='', updated_date=None, replaces_id=None):
        self.auth.require_admin()
        try:
            document = read_document(name, content, ocr=self.settings.ocr_enabled)
        except PdfInputError as exc:
            raise GuideError(str(exc)) from None
        protect_private('\n'.join([document.document_name, title, section] + [p.text for p in document.pages]))
        digest = hashlib.sha256(content).hexdigest()
        metadata, chunks = make_chunks(document, embedder, title=title or document.document_name,
                                       section=section, updated_date=updated_date, file_hash=digest)
        vectors = np.asarray(embedder.encode([chunk.text for chunk in chunks]), dtype='<f4')
        if vectors.shape != (len(chunks), DIMENSIONS) or not np.isfinite(vectors).all():
            raise GuideError('검색 벡터 형식이 잘못되었습니다. (INDEX)')
        metadata['indexed_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        metadata['last_error'] = ''
        metadata['replaces_id'] = replaces_id
        key = metadata['id'] + '.' + document.source_type
        self.store.put(key, content)
        try:
            self.auth.publish(metadata, chunks, vectors)
            if replaces_id:
                old = self._document(replaces_id)
                self.auth.retire(replaces_id)
                try:
                    self.store.delete(self._key(old))
                except GuideError:
                    pass
        except Exception:
            try:
                self.store.delete(key)
            except GuideError:
                pass
            raise
        return metadata['id']

    def reindex(self, doc_id, embedder):
        self.auth.require_admin()
        old = self._document(doc_id)
        try:
            document = read_document(old['document_name'], self.store.read(self._key(old)),
                                     ocr=self.settings.ocr_enabled)
        except PdfInputError as exc:
            raise GuideError(str(exc)) from None
        metadata, chunks = make_chunks(document, embedder, title=old.get('title', ''),
                                       section=old.get('section', ''), updated_date=old.get('updated_date'),
                                       file_hash=old['file_hash'])
        chunks = [replace(chunk, document_id=doc_id) for chunk in chunks]
        metadata.update(id=doc_id, indexed_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                        source_type=document.source_type)
        vectors = np.asarray(embedder.encode([chunk.text for chunk in chunks]), dtype='<f4')
        self.auth.reindex(metadata, chunks, vectors)

    def retire(self, doc_id):
        self.auth.require_admin()
        document = self._document(doc_id)
        self.auth.retire(doc_id)
        self.store.delete(self._key(document))

    def search(self, question, vector, doc_ids, minimum, plan=None):
        return self.auth.search(question, vector, doc_ids, minimum, plan=plan)

    def source_chunks(self, doc_id, chunk_ids=None):
        return self.auth.source_chunks(doc_id, chunk_ids)

    def source_context(self, chunk, expected_revision):
        self.ensure_revision(expected_revision)
        chunks = self.auth.source_chunks(chunk.document_id)
        current = next((item for item in chunks if item.id == chunk.id), None)
        result = neighbors(current, chunks) if current else []
        self.ensure_revision(expected_revision)
        return result

    def list_checklists(self, doc_ids):
        return self.auth.list_checklists(doc_ids)

    def save_checklist(self, entry):
        self.auth.require_admin()
        chunks = self.source_chunks(entry['document_id'])
        validated = validate_checklist(entry['title'], entry['keywords'], entry['items'], chunks)
        self.auth.save_checklist(validated)
        return validated

    def request_review(self, user_id, document_ids, chunk_ids):
        self.authorize()
        return self.auth.request_review(document_ids, chunk_ids)
