"""Web recovery: validated retry, then explicitly extractive source display."""
from copy import deepcopy
from contextvars import ContextVar
from typing import Literal
from pydantic import BaseModel
from mvp import ai
from mvp.evidence import assess_evidence
from mvp.grounding import explicit_conflicts
from mvp.query import plan_query


# Per-call state, never forwarded as public generate kwargs or written to trace.
_generation_context = ContextVar('grounded_generation_context', default=None)


def _invoke(settings, question, hits, user_id, quota, transport, plan, trace, *, capture=None, messages=None):
    token=_generation_context.set({'capture':capture,'messages':messages})
    try:
        if quota is None and transport is None:
            return ai.generate(settings,question,hits,user_id,plan=plan,trace=trace)
        return ai.generate(settings,question,hits,user_id,quota=quota,transport=transport,
                           plan=plan,trace=trace)
    finally:
        _generation_context.reset(token)


class SourceQuote(BaseModel):
    chunk_id: str
    quote: str


class SourceStatement(BaseModel):
    text: str
    evidence: list[SourceQuote]
    label: str = ''


class SourceAnswer(BaseModel):
    # Separate type: never parsed from LLM output, never represented as validated generation.
    answerable: bool = True
    statements: list[SourceStatement]
    format: Literal['bullets'] = 'bullets'
    conflict: bool = False
    answer_kind: Literal['evidence_only'] = 'evidence_only'


def evidence_only(plan, selected):
    blocked=(ai.Answer(answerable=False,statements=[]), [])
    assessment=assess_evidence(plan,selected)
    if not assessment.sufficient or explicit_conflicts(selected):
        return blocked
    used=list(assessment.hits)
    if not used or any(not h.context_complete or not h.chunk.id or not h.chunk.document_id
        or not h.chunk.document_name or not h.chunk.text.strip()
        or (h.chunk.source_type=='pdf' and (not isinstance(h.chunk.page,int) or h.chunk.page<1))
        or (h.chunk.source_type!='pdf' and not h.chunk.location) for h in used):
        return blocked
    for h in used:
        ai.protect_private(h.chunk.text)
    # Keep whole chunks and their order: never splice, trim conditions, or summarize.
    return SourceAnswer(statements=[SourceStatement(text=h.chunk.text,
        evidence=[SourceQuote(chunk_id=h.chunk.id,quote=h.chunk.text)]) for h in used]),used


def recover_answer(question, selected, plan, retry, trace):
    fallback,used=evidence_only(plan,selected)
    if not fallback.answerable:
        return fallback,used
    trace['strict_retry_attempted']=True
    try:
        answer,retry_used=retry()
        if answer.answerable:
            trace['answer_kind']='strict_grounded_retry'
            return answer,retry_used
        reason=trace.get('strict_retry',{}).get('block_reason','')
        if reason.startswith(('pre_llm:', 'after_budget:', 'citation:', 'budget_', 'citation_missing')):
            return answer,retry_used
    except ai.GuideError as exc:
        if '(AI_CONFLICT)' in str(exc):
            return ai.Answer(answerable=False,statements=[]),[]
        # A failed recovery call never bypasses quota, retries again, or exposes its text.
        trace['strict_retry_error']=type(exc).__name__
    trace.update(answer_kind='evidence_only',answerable=True,stage='evidence_only')
    return fallback,used


def generate(settings, question, hits, user_id, quota=None, transport=None, plan=None, trace=None):
    trace={} if trace is None else trace
    plan=plan or plan_query(question)
    capture={}
    try:
        answer,used=_invoke(settings,question,hits,user_id,quota,transport,plan,trace,capture=capture)
        reason=trace.get('final_validation_reason') or trace.get('validation_reason')
        eligible=not answer.answerable and reason in {'unsupported sentence','label_not_in_quote','number','unit'}
        if not eligible:
            return answer,used
    except ai.GuideError as exc:
        if '(AI_INCOMPLETE)' not in str(exc): raise
        trace['initial_failure']='AI_INCOMPLETE'
    if settings.llm_provider!='groq_free' or settings.llm_model!='openai/gpt-oss-20b':
        if trace.get('initial_failure'): raise ai.GuideError('AI 답변이 완성되기 전에 중단되었습니다. (AI_INCOMPLETE)')
        return answer,used
    selected=capture.get('selected',[])
    messages=deepcopy(capture.get('messages',[]))
    if not messages:
        return ai.Answer(answerable=False,statements=[]),[]
    messages[-1]['content'] += ('\nStrict grounded retry: Return the same JSON schema with format="bullets". '
        'Use only the provided evidence. Copy short complete source sentences verbatim. '
        'Do not add facts or change numbers, units, times, conditions, contraindications or subjects. '
        'Use empty labels. Quotes must be exact source text. Omit unsupported items.')
    retry_trace={};trace['strict_retry']=retry_trace
    def retry():
        if ai.estimated_tokens(messages,settings.llm_provider)>ai.GROQ_REQUEST_TOKEN_BUDGET:
            raise ai.GuideError('Strict retry exceeds request budget. (AI_LENGTH)')
        return _invoke(settings,question,selected,user_id,quota,transport,plan,retry_trace,messages=messages)
    return recover_answer(question,selected,plan,retry,trace)
