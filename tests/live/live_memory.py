"""Manual Memory v1 verification against the unchanged production Gemma backend.

Creates two test conversations, verifies a real explicit save/retrieve/update/
forget cycle, and saves final answers plus compact trace only. Test GPU changes
are reverted to RX 7900 XTX at the end. No reasoning traces are recorded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import httpx


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--retrieve-after-restart',metavar='REPORT',type=Path)
    args=parser.parse_args()
    output=Path(__file__).resolve().parents[2]/'external/audio-validation/results/memory-live.json'
    if args.retrieve_after_restart:
        output=output.with_name('memory-live-after-restart.json')
    rows=[]
    with httpx.Client(base_url=args.url,timeout=180,trust_env=False) as client:
        def conversation():
            r=client.post('/api/conversations',json={'title':'Memory v1 validation'})
            r.raise_for_status()
            return r.json()['conversation_id']
        def turn(cid,text):
            before=client.get('/api/trace/recent',params={'limit':500}).raise_for_status().json()['events']
            seen={json.dumps(event,sort_keys=True) for event in before}
            started=time.monotonic()
            answer=''
            with client.stream('POST','/api/chat/stream',json={'conversation_id':cid,'message':text,'mode':'chat'}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line:
                        event=json.loads(line)
                        if event['type']=='assistant.content.delta': answer+=event['delta']
            history=client.get('/api/conversations/'+cid).raise_for_status().json()
            status=history['messages'][-1]['metadata']['status']
            if status!='completed': raise RuntimeError('turn did not complete: '+status)
            trace=client.get('/api/trace/recent',params={'limit':500}).raise_for_status().json()['events']
            trace=[event for event in trace if json.dumps(event,sort_keys=True) not in seen
                   and event.get('event') in {'memory_context','tool_dispatch','tool_result','long_memory_saved','long_memory_deactivated'}]
            row={'conversation_id':cid,'prompt':text,'answer':answer,'seconds':round(time.monotonic()-started,3),'trace':trace}
            if 'Response plan:' in answer or 'I should acknowledge' in answer:
                row['answer']='[Unexpected planning text in the model final channel; not recorded]'
                rows.append(row)
                output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
                raise RuntimeError('model final channel contains planning text; remove this benchmark chat before retry')
            rows.append(row)
            output.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            print(json.dumps(row,ensure_ascii=False),flush=True)
            return row
        def memories():
            return client.get('/api/memory/long',params={'query':'основной GPU'}).raise_for_status().json()['entries']
        if args.retrieve_after_restart:
            previous=json.loads(args.retrieve_after_restart.read_text())
            cid=previous[-1]['conversation_id']
            row=turn(cid,'Какая у меня основная видеокарта?')
            assert '7900' in row['answer'] and 'XTX' in row['answer']
            row=turn(conversation(),'Какая у меня основная видеокарта?')
            assert '7900' in row['answer'] and 'XTX' in row['answer']
            assert [e for e in row['trace'] if e['event']=='memory_context'][-1]['count']==1
            return
        a,b=conversation(),conversation()
        turn(a,'Запомни, мой основной GPU RX 7900 XTX')
        gpu=memories()
        assert len(gpu)==1 and '7900' in gpu[0]['text'],gpu
        entry_id=gpu[0]['id']
        row=turn(b,'Какая у меня основная видеокарта?')
        assert '7900' in row['answer'] and 'XTX' in row['answer'],row
        context=[e for e in row['trace'] if e['event']=='memory_context']
        assert context and context[-1]['count']==1,context
        row=turn(b,'Что такое chmod?')
        assert [e for e in row['trace'] if e['event']=='memory_context'][-1]['count']==0
        turn(a,'Обнови память: теперь мой основной GPU RX 9070 XT вместо RX 7900 XTX. Сохрани только новый актуальный факт.')
        gpu=memories()
        assert len(gpu)==1 and gpu[0]['id']==entry_id and '9070' in gpu[0]['text'] and '7900' not in gpu[0]['text'],gpu
        row=turn(b,'Какая у меня основная видеокарта сейчас? Ответь кратко.')
        assert '9070' in row['answer'],row
        turn(a,'Удали из долговременной памяти факт о моём основном GPU. Забудь этот факт.')
        assert not memories(),memories()
        turn(a,'Запомни снова: мой основной GPU RX 7900 XTX')
        assert len(memories())==1 and memories()[0]['id']==entry_id
        row=turn(b,'Какая у меня основная видеокарта сейчас? Проверь актуальную память.')
        assert '7900' in row['answer'] and 'XTX' in row['answer'],row
        print('Memory cycle passed. Conversations:',a,b,flush=True)


if __name__=='__main__': main()
