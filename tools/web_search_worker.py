"""Bounded-lifetime DDGS call, not a server. Stdout is a small JSON result."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def search(query, max_results):
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, TimeoutException
    from tools.web import public_url
    try:
        with DDGS(timeout=5, verify=True) as engine:
            rows = engine.text(query, max_results=max_results, backend='auto')
    except TimeoutException:
        return {'ok': False, 'error': 'web search timed out'}
    except DDGSException as exc:
        if 'No results found' in str(exc):
            return {'ok': True, 'results': []}
        return {'ok': False, 'error': 'metasearch unavailable'}
    results, seen = [], set()
    for row in rows:
        try:
            url = str(public_url(row.get('href') or row.get('url') or ''))
        except Exception:
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({'title': str(row.get('title') or '')[:500], 'url': url,
                        'snippet': str(row.get('body') or row.get('snippet') or '')[:1200]})
        if len(results) >= max_results:
            break
    return {'ok': True, 'results': results}


if __name__ == '__main__':
    try:
        args = json.load(sys.stdin)
        result = search(args['query'], args['max_results'])
    except Exception:
        result = {'ok': False, 'error': 'search worker failed'}
    print(json.dumps(result, ensure_ascii=False))
