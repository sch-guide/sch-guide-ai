"""Evidence-type candidate routing metadata.

Routes are advisory only. They do not retrieve evidence, admit a query, or
change answerability. Image routes stay pending until reviewed image gold and a
safe vision path exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EvidenceType = Literal['text', 'table', 'image', 'mixed']


@dataclass(frozen=True)
class EvidenceRoute:
    primary: EvidenceType
    candidates: tuple[Literal['text', 'table', 'image'], ...]
    status: Literal['ready', 'pending_image_review']
    reason: str


_IMAGE_CUE = re.compile(
    r'이미지|그림|도식|도표\s*화면|화면|스크린샷|캡처|workflow|flowchart',
    re.I,
)
_TABLE_CUE = re.compile(
    r'표(?:로|에서|의|를|는|$)|비교|차이|제품별|제제별|항목별|'
    r'보관|온도|속도|용량|투여량|간격|몇\s*(?:분|시간|일|회|번)|'
    r'유효\s*기간|수치|기준값|단위',
    re.I,
)


def route_evidence(question: str, *, kind: str) -> EvidenceRoute:
    """Return generic candidates without consulting retrieval results or gold."""
    if _IMAGE_CUE.search(question):
        return EvidenceRoute(
            'image', ('image',), 'pending_image_review', 'explicit_image_or_workflow_cue'
        )
    if kind == 'comparison' or _TABLE_CUE.search(question):
        return EvidenceRoute(
            'table', ('table', 'text'), 'ready', 'structured_lookup_or_comparison_cue'
        )
    return EvidenceRoute('text', ('text',), 'ready', 'default_text_evidence')
