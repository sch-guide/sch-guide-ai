"""Narrow post-LLM surface matching. Returns original source text, never a paraphrase."""
import re

from mvp.evidence import source_sentences
from mvp.library import clean


def surface(text):
    value = clean(text)
    # Only a terminal period and one balanced outer wrapper; never internal punctuation.
    if value.endswith('.') and not re.search(r'\d\.$', value):
        value = value[:-1]
    if value.startswith('(') and value.endswith(')'):
        depth = 0
        for i, char in enumerate(value):
            depth += (char == '(') - (char == ')')
            if depth == 0 and i != len(value) - 1:
                break
        else:
            value = value[1:-1].strip()
    # A single grammatical expansion, not general stemming or particle deletion.
    value = re.sub(r'유무를 관찰한다$', '유무 관찰', value)
    return value


def target_forms(chunk):
    # The subject must be explicit in the cited chunk's section, not inferred from a question.
    if not re.fullmatch(r'(?:\d+[.)]\s*)?방사선\s*조사\s*혈액제제', clean(chunk.section)):
        return []
    result = []
    for line in source_sentences(chunk.text):
        match = re.fullmatch(r'대상혈액\s*[:：]\s*([가-힣]+),\s*([가-힣]+ 혈액제제)', line)
        if not match:
            continue
        first, second = match.groups()
        # No arbitrary object/condition/action grammar: only this explicit field template.
        result.append((line, {
            f'방사선 조사는 {first}와 {second}에 적용한다',
            f'{first}, {second}에 방사선 조사를 시행한다',
        }))
    return result


def normalized_evidence(text, evidence, sources):
    """Every output sentence needs one complete, quoted source sentence/field."""
    result = []
    for sentence in source_sentences(text):
        found = None
        for item in evidence:
            chunk = sources[item.chunk_id]
            originals = source_sentences(chunk.text)
            # Preserve the whole parenthetical unit across PDF line wraps.
            # No content inside the wrapper is discarded.
            originals += [clean(m.group()) for m in re.finditer(r'^[ \t]*\([^()]*\)[ \t]*(?=\n|$)', chunk.text, re.M)
                          if '\n' in m.group()]
            for original in originals:
                if surface(sentence) == surface(original) and surface(original) in surface(item.quote):
                    found = (original, [item])
                    break
            if found is None:
                for original, forms in target_forms(chunk):
                    if surface(sentence) in forms and clean(original) in clean(item.quote):
                        found = (original, [item])
                        break
            if found is not None:
                break
        if found is None:
            return []
        result.append(found)
    return result
