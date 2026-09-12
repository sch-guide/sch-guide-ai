"""무료 임베딩, 위치를 보존하는 분할과 문맥 검색."""

import hashlib
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass, replace
from uuid import uuid4

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

from mvp.medical_terms import ALIASES
from mvp.settings import DIMENSIONS, MODEL, ROOT, GuideError

NO_GUIDELINE = "등록된 지침서에서 확인할 수 없습니다."
SEARCH_VERSION = 9
CHUNK_VERSION = 4
# 이 단어만 겹치는 경우에는 서로 다른 시술의 문서를 근거로 채택하지 않습니다.
INTENT_TERMS = {
    "목적", "절차", "정의", "방법", "순서", "준비", "준비물", "주의", "주의사항",
    "관리", "간호", "사용", "이용", "대상", "적응증", "금기", "비교", "차이",
    "확인", "사항", "내용", "설명", "계산", "시행", "전후", "이후", "후에",
}


def clean(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def protect_private(text):
    # 보조 검사입니다. 자유롭게 적힌 환자 이름까지 모두 찾는 익명화 기능은 아닙니다.
    patterns = (
        r"(?<!\d)\d{6}\s*[-–]?\s*[1-8]\d{6}(?!\d)",
        r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)",
        r"(?:환자\s*(?:등록)?번호|등록번호|차트번호|MRN)\s*[:：#]?\s*\d{4,}",
        r"(?:환자\s*(?:성명|이름)|환자명)\s*[:：]\s*[가-힣]{2,5}",
        r"병실\s*번호\s*[:：#]\s*\d{2,}",
    )
    if any(re.search(pattern, text, re.I) for pattern in patterns):
        raise GuideError("환자 식별정보로 의심되는 내용이 있어 처리를 중단했습니다. 개인정보를 제거해 주세요. (PRIVACY)")


def terms(question):
    tokens = re.findall(r"[a-z][a-z0-9-]*|[가-힣]{2,}", clean(question).lower())
    stop = {"어떻게", "알려줘", "알려주세요", "해주세요", "해줘", "무엇인가요", "뭔가요",
            "관련", "지침", "그럼", "그것은", "대해서", "대해", "대한", "관한", "추가", "질문"}
    result = []
    for token in tokens:
        if token in stop:
            continue
        # 질문의 조사만 제거합니다. '정의' 같은 완전한 단어는 그대로 둡니다.
        if re.fullmatch(r"[가-힣]+", token) and token not in INTENT_TERMS:
            base = re.sub(r"(?:에서는|으로는|이란|에는|에서|이랑|란|은|는|을|를|이|가|에|의)$", "", token)
            if len(base) >= 2:
                token = base
        result.append(token)
    for anchor in anchors(question):
        result.extend(ALIASES[anchor])
    return list(dict.fromkeys(x for x in result if len(x) > 1))[:32]


def embedding_question(question):
    # 검색 모델에는 핵심어를 전달합니다. 원래 질문과 출처 원문은 수정하지 않습니다.
    text = " ".join(terms(question))
    # 한글 합성어의 띄어쓰기 차이를 완화합니다. 의료 동의어를 임의로 만들지 않습니다.
    return re.sub(r"([가-힣]{2,})(간호|관리|절차|목적)(?= |$)", r"\1 \2", text) or question


def term_matches(word, text):
    text = clean(text).lower()
    if re.fullmatch(r"[가-힣 ]+", word):
        return re.sub(r"\s+", "", word) in re.sub(r"\s+", "", text)
    # VRE와 CRE, PCN과 PCNA 등 서로 다른 약어를 부분 일치시키지 않습니다.
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text))


def lexical_evidence(question, chunk, words=None):
    words = terms(question) if words is None else words
    topics = [w for w in words if w not in INTENT_TERMS]
    body = {w for w in words if term_matches(w, chunk.text)}
    context = " ".join([chunk.document_name, chunk.title, chunk.section])
    topic_body = any(w in body for w in topics)
    topic_context = any(term_matches(w, context) for w in topics)
    intent_body = any(w in body for w in INTENT_TERMS)
    # 제목은 주제 연결에만 사용합니다. 본문에 질문의 내용이 있어야 키워드 근거가 됩니다.
    supported = topic_body or (topic_context and intent_body)
    score = len(body) + (0.5 if topic_context and intent_body else 0)
    return score, supported


def anchors(question):
    # 긴 용어 안의 약물명은 별도 주제로 해석하지 않습니다(VRE와 vancomycin).
    remaining, found = clean(question).lower(), []
    for key, alias in sorted(((k, a) for k, group in ALIASES.items() for a in group),
                             key=lambda item: len(item[1]), reverse=True):
        if term_matches(alias, remaining):
            found.append(key)
            pattern = (r"\s*".join(map(re.escape, alias.replace(" ", "")))
                       if re.fullmatch(r"[가-힣 ]+", alias) else
                       r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])")
            remaining = re.sub(pattern, " ", remaining)
    return list(dict.fromkeys(found))


def compatible(question, text):
    requested = anchors(question)
    if not requested:
        return True
    # 여러 약어를 비교하는 질문은 각각의 문서를 함께 검색할 수 있어야 합니다.
    return bool(set(requested) & set(anchors(text)))


def retrieval_question(question, previous_question="", follow_up=False):
    question = question.strip()
    if not question or len(question) > 500:
        raise GuideError("질문은 1~500자로 입력해 주세요.")
    protect_private(question)
    if follow_up and previous_question:
        protect_private(previous_question)
        previous = previous_question.split(" / 추가 질문: ")
        # 주제가 명시적으로 바뀌면 오래된 약어를 강제로 이어 붙이지 않습니다.
        old, new = set(anchors(previous_question)), set(anchors(question))
        if old and new and not old.intersection(new):
            return question
        # 처음 주제와 가장 최근 질문을 유지합니다. 재귀적으로 앞부분만 자르지 않습니다.
        context = previous[0][:140]
        if len(previous) > 1:
            context += " / 추가 질문: " + previous[-1][-160:]
        return f"{context} / 추가 질문: {question}"
    return question


def bounded_embedding_question(question, embedder):
    """검색어 확장 때문에 모델 입력이 넘치지 않도록 로컬에서 길이를 맞춥니다."""
    if ' / 추가 질문: ' in question:
        contexts = question.split(' / 추가 질문: ')
        # 최신 질문의 조건부터 넣어 길이를 줄여도 질문 의도가 사라지지 않게 합니다.
        question = contexts[-1] + ' ' + contexts[0] + (' ' + contexts[-2] if len(contexts) > 2 else '')
    text = embedding_question(question)
    while embedder.count(text) > 128 and text:
        text = text.rsplit(" ", 1)[0] if " " in text else text[:-1]
    if not text:
        raise GuideError("검색어를 짧게 입력해 주세요. (EMBED_LENGTH)")
    return text


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    document_name: str
    page: int | None
    title: str
    section: str
    updated_date: str | None
    text: str
    index: int
    source_type: str = "pdf"
    location: str = ""
    normalized_text: str = ""
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    parent_id: str = ""

    def __post_init__(self):
        if not self.normalized_text:
            object.__setattr__(self, 'normalized_text', clean(self.text).lower())

    @classmethod
    def from_row(cls, row):
        return cls(**{key: row[key] for key in cls.__dataclass_fields__ if key in row})


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    similarity: float
    lexical: float = 0
    bm25_score: float = 0
    fusion_score: float = 0
    rerank_score: float = 0
    context_only: bool = False
    context_complete: bool = True


class Embedder:
    """모델만 공유합니다. 문서·질문·직원 정보는 전역 캐시에 넣지 않습니다."""

    def __init__(self):
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        try:
            self.engine = TextEmbedding(
                model_name=MODEL, cache_dir=str(ROOT / "data" / "models"), threads=2,
                providers=["CPUExecutionProvider"],
            )
            # 고정한 FastEmbed 0.8.0 tokenizer를 복제합니다. 추론 설정은 바꾸지 않습니다.
            self.tokenizer = Tokenizer.from_str(self.engine.model.tokenizer.to_str())
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()
            self.lock = threading.Lock()
        except Exception:
            raise GuideError("무료 검색 모델을 준비하지 못했습니다. 첫 다운로드의 인터넷 연결과 data/models 쓰기 권한을 확인하세요. (EMBED_SETUP)") from None

    def count(self, text):
        return len(self.tokenizer.encode(text).ids)

    def encode(self, texts):
        if any(self.count(text) > 128 for text in texts):
            raise GuideError("검색 모델의 입력 길이를 넘었습니다. 질문을 짧게 쓰거나 문서를 다시 분할해 주세요. (EMBED_LENGTH)")
        try:
            with self.lock:
                vectors = np.array(list(self.engine.embed(texts, batch_size=8)), dtype=np.float32)
            if vectors.shape != (len(texts), DIMENSIONS) or not np.isfinite(vectors).all():
                raise ValueError("shape")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norms == 0).any():
                raise ValueError("empty vector")
            return vectors / norms
        except GuideError:
            raise
        except Exception:
            raise GuideError("검색 벡터를 만들지 못했습니다. 문서 크기와 메모리를 확인하세요. (EMBED)") from None


def make_chunks(document, embedder, *, title="", section="", updated_date=None, file_hash=""):
    protect_private("\n".join([document.document_name, title, section] + [p.text for p in document.pages]))
    if not document.text_page_count:
        raise GuideError("추출된 글이 없습니다. 스캔 PDF는 OCR 처리 후 등록해 주세요.")
    doc_id = str(uuid4())
    metadata = dict(id=doc_id, document_name=document.document_name, title=title or document.document_name,
                    section=section, updated_date=updated_date, file_hash=file_hash,
                    page_count=len(document.pages), model=MODEL, source_type=document.source_type)
    from mvp.pdf_layout import EXTRACTION_VERSION
    metadata.update(extraction_version=EXTRACTION_VERSION, chunk_version=CHUNK_VERSION, warnings=list(document.warnings),
                    missing_locations=[p.number if p.number is not None else p.location
                                       for p in document.pages if p.error or not p.text])
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=110, chunk_overlap=20, length_function=embedder.count,
        separators=["\n\n", "\n", ". ", " ", ""], strip_whitespace=True,
    )
    chunks = []
    for page in document.pages:
        if page.error or not page.text:
            continue
        from mvp.structure import semantic_blocks
        for block, block_section, parent in semantic_blocks(page, doc_id, section):
            header = page.header or (block.splitlines()[0] if " | " in block else "")
            header = header.strip()
            if header and embedder.count(header) <= 40 and block.strip() != header:
                # 표의 첫 행을 잘린 다음 문단에도 붙여 열 제목/단위의 연결을 보존합니다.
                budget = max(30, 106 - embedder.count(header))
                table_splitter = RecursiveCharacterTextSplitter(chunk_size=budget, chunk_overlap=min(12, budget // 4),
                    length_function=embedder.count, separators=["\n", " | ", " ", ""])
                pieces = [header + "\n" + part for part in table_splitter.split_text(block)]
                # 토큰 경계가 달라질 수 있으므로 최종 길이를 다시 검사합니다.
                texts = [piece for part in pieces for piece in splitter.split_text(part)]
            else:
                texts = splitter.split_text(block)
            for text in texts:
                chunks.append(Chunk(str(uuid4()), doc_id, document.document_name, page.number,
                                    metadata["title"], block_section, updated_date, text, len(chunks),
                                    document.source_type, page.location, parent_id=parent))
    if not chunks or len(chunks) > 5000:
        raise GuideError("문서당 1~5,000개 문단을 지원합니다. 큰 문서는 나누어 등록해 주세요.")
    chunks = [replace(chunk, previous_chunk_id=chunks[i-1].id if i else None,
                      next_chunk_id=chunks[i+1].id if i+1 < len(chunks) else None) for i, chunk in enumerate(chunks)]
    metadata['chunk_count'] = len(chunks)
    return metadata, chunks


def rank_hits(question, candidates, minimum=0.38, limit=6):
    candidates = [h for h in candidates if compatible(question, h.chunk.text + " " + h.chunk.section)]
    words = terms(question)
    supported = []
    for hit in candidates:
        lexical, exact = lexical_evidence(question, hit.chunk, words)
        # 하나의 정확한 주제어도 근거 후보가 됩니다. 단순히 임계값을 낮추지는 않습니다.
        # 키워드/문서 주제 연결이 전혀 없으면 더 높은 의미 유사도를 요구합니다.
        threshold = minimum if exact else max(minimum, 0.55)
        if exact or hit.similarity >= threshold:
            supported.append(Hit(hit.chunk, hit.similarity, lexical if exact else 0))
    candidates = supported
    dense = sorted(candidates, key=lambda h: h.similarity, reverse=True)
    lexical = sorted([h for h in candidates if h.lexical], key=lambda h: h.lexical, reverse=True)
    scores = {}
    for weight, ranking in ((1, dense), (2, lexical)):
        for rank, hit in enumerate(ranking):
            scores[hit.chunk.id] = scores.get(hit.chunk.id, 0) + weight / (60 + rank + 1)
    # 후속 질문의 현재 요청을 우선합니다. '목적' 질문에 문서 전체의 관리 문단이 앞서지 않도록 합니다.
    current_question = question.split(" / 추가 질문: ")[-1]
    focuses = [word for word in ("목적", "정의", "절차", "순서", "준비물", "주의사항", "금기", "적응증", "대상")
               if word in current_question]
    for hit in candidates:
        scores[hit.chunk.id] += .03 * sum(term_matches(word, hit.chunk.text) for word in focuses)
    seen, selected = set(), []
    for hit in sorted(candidates, key=lambda h: scores[h.chunk.id], reverse=True):
        signature = (hit.chunk.document_id, hit.chunk.page, hit.chunk.location, clean(hit.chunk.text))
        if signature not in seen:
            selected.append(hit)
            seen.add(signature)
        if len(selected) == limit:
            break
    return selected


def validate_checklist(title, keywords, items, chunks):
    if not title.strip() or not keywords or not 1 <= len(items) <= 40:
        raise GuideError("체크리스트 제목·검색어와 1~40개 항목을 입력하세요.")
    protect_private(title + " " + " ".join(keywords))
    source_map = {c.id: c for c in chunks}
    document_ids = set()
    for item in items:
        chunk = source_map.get(item.get("chunk_id"))
        quote = clean(item.get("quote", ""))
        protect_private(quote)
        if not chunk or len(quote) < 4 or quote not in clean(chunk.text):
            raise GuideError("체크 항목은 선택한 원문에서 그대로 가져와야 합니다. 출처를 다시 확인해 주세요.")
        if item.get("stage") not in {"사전 확인", "준비", "시행 전", "시행 후", "기타"}:
            raise GuideError("체크 항목의 단계를 선택하세요.")
        document_ids.add(chunk.document_id)
    if len(document_ids) != 1:
        raise GuideError("한 체크리스트는 같은 문서의 항목으로 작성하세요.")
    return dict(id=str(uuid4()), document_id=document_ids.pop(), title=title.strip(),
                keywords=keywords, items=items)


class LocalLibrary:
    """검색용 메모리 스냅샷. 운영 앱에서는 repository가 서버 측 캐시로 관리합니다."""

    def __init__(self):
        self.docs, self.chunks, self.checklists = [], [], []
        self.vectors = np.empty((0, DIMENSIONS), dtype=np.float32)
        self._index_key, self._index = None, None

    def documents(self):
        return list(self.docs)

    def publish(self, metadata, chunks, vectors):
        if any(doc["file_hash"] == metadata["file_hash"] for doc in self.docs):
            raise GuideError("같은 PDF가 이미 등록되어 있습니다.")
        if len(self.chunks) + len(chunks) > 5000:
            raise GuideError("이 PC 시험 모드는 총 5,000개 문단까지 지원합니다.")
        combined = np.concatenate([self.vectors, vectors]).astype("float32")
        self.docs.append(metadata)
        self.chunks.extend(chunks)
        self.vectors = combined
        self._index_key = None

    def search(self, question, vector, doc_ids, minimum, plan=None, trace=None):
        from mvp.retrieval import search
        return search(self, question, vector, doc_ids, minimum, plan, trace=trace)

    def source_chunks(self, doc_id, chunk_ids=None):
        return [c for c in self.chunks if c.document_id == doc_id
                and (chunk_ids is None or c.id in chunk_ids)]

    def save_checklist(self, checklist):
        self.checklists.append(checklist)

    def list_checklists(self, doc_ids):
        return [c for c in self.checklists if c["document_id"] in doc_ids]

    def retire(self, doc_id):
        keep = [i for i, c in enumerate(self.chunks) if c.document_id != doc_id]
        self.vectors = self.vectors[keep]
        self.chunks = [self.chunks[i] for i in keep]
        self.docs = [d for d in self.docs if d["id"] != doc_id]
        self.checklists = [c for c in self.checklists if c["document_id"] != doc_id]
        self._index_key = None


def fingerprint(documents, selected):
    value = [(d["id"], d.get("file_hash"), d.get("updated_date")) for d in documents if d["id"] in selected]
    return hashlib.sha256(repr(sorted(value)).encode()).hexdigest()


def chunk_payload(chunk):
    return {**asdict(chunk), 'chunk_id': chunk.id, 'page_number': chunk.page,
            'section_title': chunk.section, 'raw_text': chunk.text}
