"""Offline input mapping; explicit --run enables sequential Groq evaluator calls."""
import argparse
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
from importlib.metadata import version
import json
import logging
import math
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = ('context_precision', 'context_recall')
MODEL = 'groq/openai/gpt-oss-20b'
LEGACY_RUNNER = '5555ceebbe3159131b42a3688970f4bd527c026690d8d5444e0f12915adec938'
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


def exception_chain(exc):
    todo, seen = [exc], set()
    while todo:
        item = todo.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        todo.extend([getattr(item, '__cause__', None), getattr(item, '__context__', None)])
        for attempt in getattr(item, 'failed_attempts', ()) or ():
            todo.append(getattr(attempt, 'exception', None))
        last = getattr(item, 'last_attempt', None)
        if last is not None and hasattr(last, 'exception'):
            todo.append(last.exception())


def is_rate_limit(exc):
    return any(getattr(e, 'status_code', None) == 429
               or getattr(getattr(e, 'response', None), 'status_code', None) == 429
               or getattr(e, 'code', None) == 'rate_limit_exceeded'
               or 'rate_limit_exceeded' in str(e) for e in exception_chain(exc))


def header_delay(headers):
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


def retry_after(exc):
    if not is_rate_limit(exc):
        return None
    delays = []
    for e in exception_chain(exc):
        for headers in (getattr(e, 'headers', {}),
                        getattr(getattr(e, 'response', None), 'headers', {})):
            delay = header_delay(headers or {})
            if delay is not None:
                delays.append(delay)
    return max(delays) if delays else None


async def with_retry(call, sleep=asyncio.sleep, budget=None, emit=print):
    budget = [2] if budget is None else budget
    while True:
        try:
            return await call()
        except Exception as exc:
            if not is_rate_limit(exc) or budget[0] <= 0:
                raise
            budget[0] -= 1
            delay = retry_after(exc)
            kind = 'fallback after 429' if delay is None else 'provider Retry-After'
            delay = 60 if delay is None else max(1, delay)
            while delay > 0:
                emit(f'{kind}: waiting {delay:.0f}s')
                step = min(delay, 60)
                await sleep(step)
                delay -= step


class RequestPacer:
    """Local rolling token estimate; provider 429 remains authoritative."""
    def __init__(self, sleep=asyncio.sleep, clock=time.time, emit=print, ledger=None):
        self.sleep, self.clock, self.emit = sleep, clock, emit
        self.ledger = [] if ledger is None else ledger

    def finished(self, tokens):
        self.ledger.append({'at':self.clock(), 'tokens':tokens})

    async def wait(self, tokens):
        if tokens > 8000:
            raise ValueError('estimated_request_exceeds_tpm')
        now=self.clock()
        self.ledger[:] = [x for x in self.ledger if x['at'] > now-60]
        used=sum(x['tokens'] for x in self.ledger)
        delay=0
        for item in sorted(self.ledger, key=lambda x:x['at']):
            if used+tokens <= 8000: break
            used-=item['tokens']
            delay=max(delay,item['at']+60-now)
        if delay>0:
            self.emit(f'normal pacing: waiting {delay:.0f}s')
            await self.sleep(delay)


def recover(report):
    for row in report['results']:
        for name in METRICS:
            if row['metric_status'][name]=='in_progress':
                row['metric_status'][name]='pending'
                row.setdefault('recovery_history', []).append(name)


def cached_judge(judge, records, active, checkpoint, emit):
    async def generate(prompt, model):
        index=active['index']; active['index']+=1
        digest=hashlib.sha256((model.__name__+'\n'+prompt).encode()).hexdigest()
        if index < len(records):
            record=records[index]
            if record['input_sha256']!=digest:
                raise ValueError('Judge checkpoint input mismatch')
            if record['status']=='completed':
                return model.model_validate(record['result'])
        else:
            record=dict(index=index+1,input_sha256=digest,status='pending')
            records.append(record)
        record['status']='in_progress'; checkpoint()
        emit(f'context {index+1}/{active["total"]} running')
        result=await judge(prompt,model)
        # Only the verdict is needed for precision; omit free-form reason text.
        payload=result.model_dump(mode='json')
        if 'verdict' in payload: payload={'verdict':payload['verdict'],'reason':''}
        record.update(status='completed',result=payload)
        checkpoint()
        emit(f'context {index+1}/{active["total"]} completed')
        return result
    return generate


def validate_resume(old, new):
    if {k:v for k,v in old.items() if k != 'runner_sha256'} != {k:v for k,v in new.items() if k != 'runner_sha256'}:
        raise ValueError('Resume evaluation inputs/settings differ.')
    if old['runner_sha256'] not in (new['runner_sha256'], LEGACY_RUNNER,
            '5346661feb4c97e30d0c207262a376cbf05f169a168b1470ad363c9f51cbcea2'):
        raise ValueError('Unknown runner revision; explicit compatibility review required.')


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
              'Resume restores stale in_progress metrics and reuses persisted judge results.']
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def report_path(output):
    return (output.parent/'CURRENT_HYBRID_RAGAS_BASELINE.md'
            if output.name == 'current_hybrid_ragas_baseline.json' else output.with_suffix('.md'))


async def evaluate(report, score, output):
    recover(report)
    for row in report['results']:
        for name in METRICS:
            if row['metric_status'][name] not in ('pending', 'error'):
                continue
            row['metric_status'][name] = 'in_progress'
            print(f'{row["question_id"]} {name} running')
            save(report, output)
            try:
                value = float(await score(name, row))
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError('Invalid metric value')
                row[name] = value
                row['metric_status'][name] = 'completed'
                row['reason'][name] = None
            except Exception as exc:
                row['metric_status'][name] = 'pending' if is_rate_limit(exc) else 'error'
                # Never serialize exception messages, headers, prompts, or credentials.
                row['reason'][name] = ('rate_limit_retry_exhausted' if is_rate_limit(exc)
                                       else 'evaluator_error_' + type(exc).__name__)
            print(f'{row["question_id"]} {name} {row["metric_status"][name]}')
            save(report, output)
            markdown(report, report_path(output))


def scorer(sleep=asyncio.sleep, report=None, checkpoint=lambda:None):
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
    for logger in ('instructor', 'instructor.v2.retry', 'LiteLLM'):
        logging.getLogger(logger).disabled = True
    gate = asyncio.Semaphore(1)
    active = {'id':'-', 'metric':'-'}
    budget = [2]
    def emit(message): print(f'{active["id"]} {active["metric"]} {message}')
    ledger=[] if report is None else report.setdefault('token_window',[])
    pace = RequestPacer(sleep=sleep, emit=emit,ledger=ledger)

    async def client(*args, **kwargs):
        # Serialize even if a metric internally creates concurrent tasks.
        async with gate:
            kwargs.update(api_key=key, num_retries=0)
            # Local tokenizer estimate with 25% margin plus output reserve.
            # Does not change the provider request or the metric prompt.
            estimate=math.ceil(litellm.token_counter(model=MODEL,
                messages=kwargs.get('messages',[]))*1.25)+2048
            async def request():
                await pace.wait(estimate)
                emit('running')
                response=None
                try:
                    response=await litellm.acompletion(*args, **kwargs)
                    return response
                finally:
                    usage=getattr(getattr(response,'usage',None),'total_tokens',None)
                    pace.finished(max(estimate,usage if isinstance(usage,int) else 0))
                    checkpoint()
            return await with_retry(request, sleep=sleep, budget=budget, emit=emit)

    structured_client = instructor.from_litellm(client, mode=instructor.Mode.JSON)
    llm = llm_factory(MODEL, provider='groq', adapter='litellm', client=structured_client,
                      temperature=0, max_retries=1)
    metrics = {'context_precision':ContextPrecision(llm=llm), 'context_recall':ContextRecall(llm=llm)}

    async def score(name, row):
        active.update(id=row.get('question_id', '-'), metric=name)
        budget[0] = 2  # Shared across all subrequests of this question/metric.
        records=row.setdefault('context_progress',{}).setdefault(name,[])
        progress={'index':0,'total':len(row['retrieved_contexts']) if name=='context_precision' else 1}
        original=llm.agenerate
        llm.agenerate=cached_judge(original,records,progress,checkpoint,emit)
        try:
            result = await metrics[name].ascore(user_input=row['question'], reference=row['reference'],
                                              retrieved_contexts=row['retrieved_contexts'])
        finally:
            llm.agenerate=original
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
            validate_resume(report['identity'], identity)
            backup=args.output.with_suffix('.json.pre-context-v2.bak')
            if not backup.exists():
                with backup.open('xb') as file:
                    file.write(args.output.read_bytes())
            report.setdefault('resume_runner_history', []).append(identity['runner_sha256'])
        else:
            report = dict(identity=identity, retrieval_name=args.retrieval_name, results=rows)
            with args.output.open('x', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        asyncio.run(evaluate(report, scorer(report=report,checkpoint=lambda:save(report,args.output)), args.output))
        markdown(report, report_path(args.output))
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('RAGAS runner stopped: ' + type(exc).__name__)
        raise SystemExit(2)
