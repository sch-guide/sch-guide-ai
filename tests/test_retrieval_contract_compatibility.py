from src.library import fingerprint
from tools import (
    bm25_only_evaluate,
    chromadb_only_evaluate,
    current_hybrid_evaluate,
    retrieval_baseline_metrics,
)

FROZEN_FINGERPRINT_ALGORITHM_ID = "mvp.library.fingerprint_sha256"


def test_src_fingerprint_preserves_the_frozen_sha256_result() -> None:
    documents = [
        {"id": "doc-b", "file_hash": "bbb", "updated_date": "2026-09-02"},
        {"id": "doc-a", "file_hash": "aaa", "updated_date": "2026-09-01"},
    ]

    assert fingerprint(documents, ["doc-a", "doc-b"]) == (
        "b08614c14bbc6cca8157be4f4f82163859368646067655377fbca3102baedc79"
    )


def test_retrieval_evaluators_share_the_frozen_fingerprint_algorithm_id() -> None:
    assert (
        getattr(retrieval_baseline_metrics, "FINGERPRINT_ALGORITHM_ID", None)
        == FROZEN_FINGERPRINT_ALGORITHM_ID
    )
    assert all(
        getattr(module, "FINGERPRINT_ALGORITHM_ID", None)
        == retrieval_baseline_metrics.FINGERPRINT_ALGORITHM_ID
        for module in (
            bm25_only_evaluate,
            chromadb_only_evaluate,
            current_hybrid_evaluate,
        )
    )
