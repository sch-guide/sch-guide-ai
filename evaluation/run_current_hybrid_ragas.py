"""Offline input mapping; explicit --run enables sequential Groq evaluator calls."""
import argparse
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ('context_precision', 'context_recall')
MODEL = 'groq/openai/gpt-oss-20b'
PINS = {'ragas': '0.4.3', 'litellm': '1.101.0', 'langchain-community':'0.4.1',
        'instructor':'1.17.0', 'fsspec':'2026.6.0'}


def map_rows(labels, saved):
    rows = saved['results']
    by_id = {r['question_id']: r for r in rows}
    if len(by_id) != len(rows) or len({c['id'] for c in labels}) != len(labels) or set(by_id) != {c['id'] for c in labels}:
        raise ValueError('Question IDs differ or are duplicated.')
    result = []
    for case in labels:
        row = by_id[case['id']]
        if row['question'] != case['question']:
            raise ValueError('Question text mismatch.')
        contexts = [c['content'] for c in row['retrieved_contexts']]
        if any(not isinstance(c, str) or not c.strip() for c in contexts):
            raise ValueError('Invalid saved context; do not synthesize replacements.')
        result.append(dict(question_id=case['id'], question=case['question'],
            reference=case['reference_answer'], retrieved_contexts=contexts,
            context_precision=None, context_recall=None, faithfulness=None, answer_relevancy=None,
            metric_status={**{m: 'pending' if contexts else 'na' for m in METRICS},
                           'faithfulness':'na', 'answer_relevancy':'na'},
            reason={**{m: None if contexts else 'no_retrieved_contexts' for m in METRICS},
                    'faithfulness':'not_evaluated_service_response_unavailable',
                    'answer_relevancy':'not_evaluated_service_response_unavailable'}))
    return result


def retry_after(exc):
    if getattr(exc, 'status_code', None) != 429:
        return None
    headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
    value = headers.get('retry-after') or headers.get('Retry-After')
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError):
            return None
    return max(0, delay) if math.isfinite(delay) else None


async def with_retry(call, sleep=asyncio.sleep):
    for attempt in range(3):
        try:
            return await call()
        except Exception as exc:
            delay = retry_after(exc)
            if attempt == 2 or delay is None:
                raise
            while delay > 0:
                print(f'waiting for evaluator rate limit {delay:.0f}')
                step = min(delay, 60)
                await sleep(step)
                delay -= step


def save(report, path):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def mean(report, metric):
    values = [r[metric] for r in report['results'] if r['metric_status'][metric] == 'completed']
    return (sum(values)/len(values), len(values)) if values else (None, 0)


def markdown(report, path):
    lines = ['# Current Hybrid RAGAS Baseline', '',
        'Retrieval: ' + report.get('retrieval_name', 'Current Hybrid Retrieval'),
        '', 'Evaluator: `' + MODEL + '`; RAGAS 0.4.3; LiteLLM 1.101.0.',
        'Stored contexts only. N/A excluded from means. Pending is not a score.', '',
        '| Metric | Mean | Valid Questions |', '| --- | ---: | ---: |']
    for name, title in [('context_precision','Context Precision'), ('context_recall','Context Recall')]:
        value, count = mean(report, name)
        lines.append(f'| {title} | {value:.6f} | {count} |' if value is not None else f'| {title} | N/A | 0 |')
    lines += ['| Faithfulness | N/A | 0 |', '| Answer Relevancy | N/A | 0 |', '',
              '| ID | Context Precision | Context Recall | Status |', '| -- | --: | --: | -- |']
    for row in report['results']:
        values = ['N/A' if row[m] is None else f'{row[m]:.6f}' for m in METRICS]
        status = '; '.join(f'{m}: {row["metric_status"][m]} ({row["reason"].get(m) or "-"})' for m in METRICS)
        lines.append(f'| {row["question_id"]} | {values[0]} | {values[1]} | {status} |')
    lines += ['', 'Empty contexts (not scored): ' + ', '.join(r['question_id'] for r in report['results'] if not r['retrieved_contexts']),
              '', 'Faithfulness/Answer Relevancy are intentionally not evaluated.',
              'An in_progress metric has an uncertain outcome and is not automatically replayed.']
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def report_path(output):
    return (output.parent/'CURRENT_HYBRID_RAGAS_BASELINE.md'
            if output.name == 'current_hybrid_ragas_baseline.json' else output.with_suffix('.md'))


async def evaluate(report, score, output):
    for row in report['results']:
        for name in METRICS:
            if row['metric_status'][name] not in ('pending', 'error'):
                continue
            row['metric_status'][name] = 'in_progress'
            save(report, output)
            try:
                value = float(await score(name, row))
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError('Invalid metric value')
                row[name] = value
                row['metric_status'][name] = 'completed'
                row['reason'][name] = None
            except Exception as exc:
                row['metric_status'][name] = 'error'
                # Never serialize exception messages, headers, prompts, or credentials.
                row['reason'][name] = 'evaluator_error_' + type(exc).__name__
            save(report, output)
            markdown(report, report_path(output))


def scorer():
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    from dotenv import dotenv_values
    import litellm
    import instructor
    from ragas.llms import llm_factory
    from ragas.metrics.collections import ContextPrecision, ContextRecall
    key = dotenv_values(ROOT / 'mvp' / '.env').get('GUIDE_LLM_API_KEY')
    if not key:
        raise ValueError('GUIDE_LLM_API_KEY unavailable in mvp/.env')
    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    gate = asyncio.Semaphore(1)

    async def client(*args, **kwargs):
        # Serialize even if a metric internally creates concurrent tasks.
        async with gate:
            kwargs.update(api_key=key, num_retries=0)
            return await with_retry(lambda: litellm.acompletion(*args, **kwargs))

    structured_client = instructor.from_litellm(client, mode=instructor.Mode.JSON)
    llm = llm_factory(MODEL, provider='groq', adapter='litellm', client=structured_client,
                      temperature=0, max_retries=1)
    metrics = {'context_precision':ContextPrecision(llm=llm), 'context_recall':ContextRecall(llm=llm)}

    async def score(name, row):
        result = await metrics[name].ascore(user_input=row['question'], reference=row['reference'],
                                          retrieved_contexts=row['retrieved_contexts'])
        return result.value
    return score


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--input', type=Path, default=ROOT/'evaluation/results/current_hybrid_groq_v5_completed.json')
    parser.add_argument('--labels', type=Path, default=ROOT/'evaluation/label_set.json')
    parser.add_argument('--output', type=Path, default=ROOT/'evaluation/results/current_hybrid_ragas_baseline.json')
    parser.add_argument('--retrieval-name', default='Current Hybrid Retrieval')
    args = parser.parse_args(argv)
    installed = {p:version(p) for p in PINS}
    if installed != PINS:
        raise ValueError('Install evaluation/requirements-ragas.txt exact versions.')
    def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    identity = dict(label_sha256=digest(args.labels), input_sha256=digest(args.input),
                    versions=installed, evaluator=MODEL, temperature=0, metrics=list(METRICS),
                    runner_sha256=digest(Path(__file__)))
    rows = map_rows(json.loads(args.labels.read_text(encoding='utf-8')),
                    json.loads(args.input.read_text(encoding='utf-8')))
    if not args.run:
        print(json.dumps({'ready':True, 'questions':len(rows), 'versions':installed, 'api_called':False}))
        return
    if args.output.resolve() in (args.input.resolve(), args.labels.resolve()):
        raise ValueError('Output must not overwrite input.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lock = args.output.with_suffix('.lock')
    with lock.open('x'):
        pass
    try:
        if args.resume:
            report = json.loads(args.output.read_text(encoding='utf-8'))
            if report['identity'] != identity:
                raise ValueError('Resume inputs/versions/script differ.')
        else:
            report = dict(identity=identity, retrieval_name=args.retrieval_name, results=rows)
            with args.output.open('x', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        asyncio.run(evaluate(report, scorer(), args.output))
        markdown(report, report_path(args.output))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RAGAS runner stopped: ' + type(exc).__name__)
        raise SystemExit(2)
