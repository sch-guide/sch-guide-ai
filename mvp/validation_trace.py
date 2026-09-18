"""Failure-only internal diagnostics; never participates in answer acceptance."""
import os
import re

from dotenv import dotenv_values
from mvp.library import clean, protect_private
from mvp.settings import ROOT


def safe_text(value):
    """Conservatively omit identifying/authentication text, rather than partially mask it."""
    if not value:
        return value
    try:
        protect_private(value)
    except ValueError:
        return None
    if re.search(r'환자|성명|생년|주소|병실|연락처|이메일|주민|차트|MRN|'
                 r'authorization|bearer|api[_ -]?key|secret|password|token|'
                 r'@|https?://|[A-Za-z0-9_\-]{20,}', value, re.I):
        return None
    # Never copy an available process credential even when it has an unusual format.
    credentials = list(dotenv_values(ROOT / 'mvp' / '.env').items()) + list(os.environ.items())
    if any(secret and secret in value for key, secret in credentials
           if re.search(r'KEY|TOKEN|SECRET|PASSWORD|AUTH', key, re.I)):
        return None
    return value


def record_failure(trace, reason, statement, statement_index, sources):
    """Best-effort diagnostics only. Failure here must not alter the validator outcome."""
    from mvp.evidence import source_sentences

    try:
        kind = 'other_validation_failure'
        stage = 'answer_validation'
        sentence_index = None
        sentence = None
        comparisons = []
        if statement is not None:
            for index, evidence in enumerate(statement.evidence, 1):
                chunk = sources.get(evidence.chunk_id)
                comparisons.append({
                    'evidence_index': index,
                    # Model-supplied unknown IDs might themselves contain secrets.
                    'chunk_id': safe_text(chunk.id) if chunk else None,
                    'document_id': safe_text(chunk.document_id) if chunk else None,
                    'chunk_found': chunk is not None,
                    'quote_in_chunk': bool(chunk and clean(evidence.quote) in clean(chunk.text)),
                })
        if reason == 'citation':
            kind, stage = 'citation_mismatch', 'citation_matching'
        elif reason == 'schema_or_privacy':
            kind, stage = 'schema_or_privacy', 'schema_or_privacy'
        elif reason == 'unsupported sentence' and statement is not None:
            stage = 'sentence_matching'
            for number, part in enumerate(source_sentences(statement.text), 1):
                matches = []
                for evidence, comparison in zip(statement.evidence, comparisons):
                    chunk = sources.get(evidence.chunk_id)
                    in_source = bool(chunk and part in source_sentences(chunk.text))
                    in_quote = part in clean(evidence.quote)
                    comparison.update(sentence_in_chunk=in_source, sentence_in_quote=in_quote)
                    matches.append(in_source and in_quote)
                if not any(matches):
                    sentence_index, sentence = number, part
                    kind = ('sentence_not_in_quote' if any(c['sentence_in_chunk'] for c in comparisons)
                            else 'sentence_not_in_source')
                    break
            else:
                kind, stage = 'label_not_in_quote', 'label_matching'
        trace['post_llm_validation_stage'] = stage
        original = statement.text if statement is not None else None
        safe_original = safe_text(original)
        trace['validation_failure'] = {
            'statement_index': statement_index,
            'sentence_index': sentence_index,
            'statement_text': safe_original,
            'sentence_text': safe_text(sentence) if safe_original is not None else None,
            'label': safe_text(statement.label) if statement is not None and safe_original is not None else None,
            'text_redacted': original is not None and safe_original is None,
            'failure_type': kind,
            'evidence': comparisons,
        }
    except Exception:
        trace['post_llm_validation_stage'] = 'diagnostic_unavailable'
        trace['validation_failure'] = {'failure_type': 'diagnostic_unavailable'}
