import re
import sys
from pathlib import Path

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

DATA_DIR = Path(__file__).resolve().parent / "data"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100
TOP_K = 5


def tokenize(text: str) -> list[str]:
    """BM25 검색에 사용할 간단한 한국어/영문/숫자 토크나이저."""
    return re.findall(r"[가-힣a-z0-9]+", text.lower())


def split_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """텍스트를 일정한 글자 수로 나누되 앞 조각의 끝부분을 겹친다."""
    if chunk_size <= 0:
        raise ValueError("chunk_size는 0보다 커야 합니다.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap은 0 이상 chunk_size 미만이어야 합니다.")

    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    step = chunk_size - overlap
    for start in range(0, len(normalized), step):
        chunk = normalized[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(normalized):
            break
    return chunks


def load_pdf_documents(data_dir: Path) -> list[dict[str, str | int]]:
    """data 폴더의 모든 PDF에서 페이지별 텍스트 조각을 생성한다."""
    documents: list[dict[str, str | int]] = []
    pdf_paths = sorted(
        (path for path in data_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: str(path).lower(),
    )

    if not pdf_paths:
        print(f"PDF 파일을 찾지 못했습니다: {data_dir}")
        return documents

    for pdf_path in pdf_paths:
        print(f"PDF 읽는 중: {pdf_path.name}")
        try:
            reader = PdfReader(pdf_path)
            for page_number, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text() or ""
                except Exception as error:
                    print(f"  텍스트 추출 실패 (페이지 {page_number}): {error}")
                    continue

                for chunk in split_text(page_text):
                    documents.append(
                        {
                            "text": chunk,
                            "source": pdf_path.name,
                            "page": page_number,
                        }
                    )
        except Exception as error:
            print(f"  PDF 읽기 실패: {error}")

    print(f"생성된 전체 문단 수: {len(documents)}")
    return documents


def print_search_results(
    query: str,
    documents: list[dict[str, str | int]],
    bm25: BM25Okapi,
) -> None:
    scores = bm25.get_scores(tokenize(query))
    results = sorted(
        zip(documents, scores),
        key=lambda item: float(item[1]),
        reverse=True,
    )[:TOP_K]

    for rank, (document, score) in enumerate(results, start=1):
        print("=" * 70)
        print(f"순위: {rank}")
        print(f"BM25 점수: {float(score):.3f}")
        print(f"파일명: {document['source']}")
        print(f"페이지: {document['page']}")
        print(f"원문: {document['text']}")


def main() -> None:
    # 일부 Windows 콘솔에서 PDF의 특수문자 때문에 출력이 중단되지 않게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    documents = load_pdf_documents(DATA_DIR)
    if not documents:
        print("검색할 문단이 없어 종료합니다.")
        return

    bm25 = BM25Okapi([tokenize(str(document["text"])) for document in documents])

    while True:
        try:
            query = input("\n질문을 입력하세요 (종료: q): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n검색을 종료합니다.")
            break

        if query.lower() in {"q", "quit", "exit"}:
            print("검색을 종료합니다.")
            break
        if not query:
            print("질문을 입력해주세요.")
            continue

        print_search_results(query, documents, bm25)


if __name__ == "__main__":
    main()
