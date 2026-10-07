"""Explicit b11429 per-request budget benchmark; never saves reasoning traces.

Run: python tests/live/llama_reasoning_budget_abc.py
Uses the identical Bayes prompt/oracle from the off/auto live benchmark.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import socket
import time

import httpx

from llama_reasoning_ab import SYSTEM, PROMPT, oracle, evaluate
import config
from core.llama_cpp_process import LlamaCppProcessManager
from core.provider_factory import create_provider
from core.providers.llama_cpp_provider import LlamaCppProvider
from core.runtime_settings import RuntimeSettingsService
from storage.settings_store import SettingsStore


def token_counts(client, thinking, content):
    """Count text tokens with the actual model tokenizer, never chars/4.

    Retokenization is labelled explicitly. The server does not expose
    separate reasoning usage in b11429.
    """
    def tokens(text):
        response = client.post('/tokenize', json={'content': text, 'add_special': False,
                                                 'parse_special': False})
        response.raise_for_status()
        return len(response.json()['tokens'])
    return {'reasoning_tokens': tokens(thinking), 'final_answer_tokens': tokens(content),
            'token_count_method': 'model /tokenize of separate text; excludes control/EOS tokens'}


def evaluate_final(content, finish_reason):
    strict = evaluate(content, oracle())
    fenced = re.fullmatch(r'\s*```(?:json)?\s*(.*?)\s*```\s*', content, re.DOTALL)
    blocks = list(re.finditer(r'```json\s*(.*?)\s*```', content, re.DOTALL))
    text = fenced.group(1) if fenced else blocks[-1].group(1) if blocks else content
    parsed = evaluate(text, oracle())
    try:
        answer = json.loads(text)
        complete = (finish_reason == 'stop' and isinstance(answer, dict)
                    and set(oracle()['values']).union({'optimal_strategy', 'policy', 'explanation'}) <= answer.keys())
    except ValueError:
        complete = False
        answer = {}
    return {'strict_json': strict['checks']['json'], 'full_final_answer': complete,
            'quality': parsed, 'numeric_fields_correct': sum(parsed['checks'][k] for k in oracle()['values']),
            'strategy_correct': answer.get('optimal_strategy') == oracle()['optimal_strategy'],
            'policy_correct': answer.get('policy') == oracle()['policy']}


def measure(provider):
    body = provider._payload([{'role': 'system', 'content': SYSTEM},
                              {'role': 'user', 'content': PROMPT}], None, None, None, 'auto', stream=True)
    started = time.monotonic()
    thinking = content = ''
    usage = timings = None
    reason = None
    first_content = None
    with httpx.Client(base_url=provider.config.url, timeout=300, trust_env=False) as client:
        with client.stream('POST', '/v1/chat/completions', json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith('data: '):
                    continue
                if line[6:] == '[DONE]':
                    break
                event = json.loads(line[6:])
                if event.get('error'):
                    raise RuntimeError('server returned streaming error')
                usage = event.get('usage', usage)
                timings = event.get('timings', timings)
                for choice in event.get('choices', []):
                    delta = choice.get('delta', {})
                    thinking += delta.get('reasoning_content') or ''
                    text = delta.get('content') or ''
                    if text and first_content is None:
                        first_content = time.monotonic() - started
                    content += text
                    reason = choice.get('finish_reason') or reason
        elapsed = time.monotonic() - started
        assert usage, 'b11429 usage trailer missing'
        counts = token_counts(client, thinking, content)
    # Never serialize thinking, __verbose, raw token IDs, or raw SSE.
    return {**counts, 'latency_seconds': elapsed, 'first_content_seconds': first_content,
            'completion_tokens': usage['completion_tokens'], 'prompt_tokens': usage['prompt_tokens'],
            'finish_reason': reason, **evaluate_final(content, reason),
            'final_answer': content, 'usage': usage, 'timings': timings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=config.BASE_DIR / 'external/gemma4-validation/reasoning-budget-abc')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    user_file = config.SETTINGS_STORE_PATH
    digest = hashlib.sha256(user_file.read_bytes()).hexdigest()
    store = SettingsStore(args.output / 'test-settings.json')
    store.replace({'provider': 'llama_cpp', 'llama_cpp_managed': True, 'llama_cpp_profile': 'normal',
                   'chat_output_tokens': 8192, 'request_timeout_seconds': 300})
    snapshot = RuntimeSettingsService(store).current()
    result = {'system': SYSTEM, 'prompt': PROMPT, 'reference': oracle(),
              'generation': {'temperature': 0, 'seed': 42, 'max_tokens': 8192}, 'runs': []}
    for budget in (512, 1024, 2048):
        manager = LlamaCppProcessManager(snapshot, args.output / str(budget))
        run = {'reasoning_budget_tokens': budget}
        result['runs'].append(run)
        try:
            run['diagnostics'] = manager.start()
            print(budget, 'READY', run['diagnostics']['pid'], flush=True)
            warmup = create_provider(snapshot, num_predict=128, generation_options={'temperature': 0, 'seed': 42})
            warmup.generate('Ответь числом: 2+2.', system=SYSTEM)
            base = create_provider(snapshot, generation_options={'temperature': 0, 'seed': 42})
            provider = LlamaCppProvider(replace(base.config, reasoning_budget_tokens=budget))
            run.update(measure(provider))
            print(budget, json.dumps({k: run[k] for k in ('reasoning_tokens', 'final_answer_tokens',
                  'latency_seconds', 'completion_tokens', 'finish_reason', 'full_final_answer', 'quality')},
                  ensure_ascii=False), flush=True)
        finally:
            run['stop'] = manager.stop()
            manager.close()
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((snapshot.get('llama_cpp_host'), snapshot.get('llama_cpp_port')))
            run['pid_exists_after_stop'] = (Path('/proc') / str(run.get('diagnostics', {}).get('pid'))).exists()
            (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert all(run['diagnostics']['command'] == result['runs'][0]['diagnostics']['command'] for run in result['runs'])
    result['user_settings_unchanged'] = hashlib.sha256(user_file.read_bytes()).hexdigest() == digest
    assert result['user_settings_unchanged'] and all(not run['pid_exists_after_stop'] for run in result['runs'])
    (args.output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print('A/B/C COMPLETE; SERVERS STOPPED; NO REASONING TRACE SAVED', flush=True)


if __name__ == '__main__':
    main()
