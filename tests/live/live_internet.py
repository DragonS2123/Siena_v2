"""Manual Internet smoke test against an already running Siena backend.

Uses production model/sampling settings. Saves final answers, source metadata
and compact tool trace only; never records page HTML or reasoning traces.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--non-stream', action='store_true', help='Also test the alternate /api/chat path')
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[2] / 'external/audio-validation/results/internet-live-rerun.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with httpx.Client(base_url=args.url.rstrip('/'), timeout=180, trust_env=False) as client:
        def turn(prompt, conversation=None):
            if conversation is None:
                response = client.post('/api/conversations', json={'title': 'Internet validation'})
                response.raise_for_status()
                conversation = response.json()['conversation_id']
            before = client.get('/api/trace/recent', params={'limit': 500}).json()['events']
            seen = {json.dumps(event, sort_keys=True) for event in before}
            start = time.monotonic()
            payload = {'conversation_id': conversation, 'message': prompt, 'mode': 'chat'}
            if args.non_stream:
                response = client.post('/api/chat', json=payload)
                response.raise_for_status()
                reply = response.json()
            else:
                content = ''
                with client.stream('POST', '/api/chat/stream', json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event['type'] == 'assistant.content.delta':
                            content += event['delta']
                history = client.get('/api/conversations/' + conversation).json()
                metadata = history['messages'][-1]['metadata']
                if metadata.get('status') != 'completed':
                    raise RuntimeError(f"Streaming turn did not complete: {metadata.get('status')}")
                reply = {'answer': content, 'sources': metadata.get('sources', [])}
            trace = client.get('/api/trace/recent', params={'limit': 500}).json()['events']
            row = {'prompt': prompt, 'conversation_id': conversation,
                   'seconds': round(time.monotonic() - start, 3), 'answer': reply['answer'],
                   'sources': reply.get('sources', []),
                   'tool_trace': [event for event in trace if json.dumps(event, sort_keys=True) not in seen
                                  and event.get('event') in {'tool_dispatch', 'tool_result'}]}
            rows.append(row)
            output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(row, ensure_ascii=False), flush=True)
            return conversation

        conversation = turn('Какая сегодня погода?')
        # Explicit benchmark location, not the user's inferred/current location.
        turn('Для этого теста интересует Казань. Какая сегодня там погода? Проверь актуальные данные.', conversation)
        turn('Какая последняя стабильная версия Bazzite?')
        turn('Что такое chmod?')
        turn('Проверь в интернете последнюю версию Mesa')


if __name__ == '__main__':
    main()
