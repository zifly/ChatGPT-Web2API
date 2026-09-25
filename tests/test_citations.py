"""Synthetic reference fixtures; no account data, business prompts or network."""
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from chatgpt_web2api.api_server import APIServer
from chatgpt_web2api.backend_projection import CONVERSATION_PROJECTION_JS
from chatgpt_web2api.citations import annotations_for_node
from chatgpt_web2api.config import Config
from chatgpt_web2api.turn_anchor import TurnAnchor, select_text_for_turn
from tests.browser_fixtures import driver_for

TEXT = '  中文 answer citeturn0search0turn0search1. Again citeturn0search0\n'


def references():
    return [{'type': 'grouped_webpages', 'matched_text': 'citeturn0search0turn0search1', 'items': [
        {'title': 'First', 'url': 'https://example.org/first', 'snippet': 'not projected',
         'refs': [{'turn_index': 0, 'ref_type': 'search', 'ref_index': 0}]},
        {'title': 'Second', 'url': 'https://example.org/second',
         'refs': [{'turn_index': 0, 'ref_type': 'search', 'ref_index': 1}]},
    ]}]


def mapping():
    def node(role, text, parent=None, children=None, metadata=None):
        return {'parent': parent, 'children': children or [], 'message': {
            'author': {'role': role}, 'content': {'content_type': 'text', 'parts': [text]},
            'end_turn': role == 'assistant', 'create_time': 100,
            'metadata': metadata or {},
        }}
    return {'mapping': {
        'u': node('user', 'request', children=['a']),
        'a': node('assistant', TEXT, parent='u', metadata={'content_references': references(),
                                                        'private_field': 'must not cross projection'}),
        'unrelated': node('assistant', TEXT, metadata={'content_references': [
            {'matched_text': 'citeturn0search0', 'url': 'https://example.org/wrong-turn'}]}),
    }, 'current_node': 'a'}


def project(raw):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required to execute the browser projection fixture')
    script = "const fs=require('fs');const raw=JSON.parse(fs.readFileSync(0,'utf8'));" \
             "const __D={conv_id:'synthetic',token:'synthetic',limit:50};" \
             "global.fetch=async()=>({ok:true,json:async()=>raw});" \
             + CONVERSATION_PROJECTION_JS + '.then(x=>process.stdout.write(x));'
    result = subprocess.run([node, '-e', script], input=json.dumps(raw).encode(),
                            capture_output=True, check=True, timeout=10)
    return json.loads(result.stdout)


def selected(projected):
    return select_text_for_turn(projected, TurnAnchor(
        sent_text='request', mode='captured_id', captured_user_message_id='u'))


def test_actual_js_preserves_only_small_reference_fields_and_selected_turn():
    projected = project(mapping())
    assert 'citation_references' not in projected['nodes']['u']
    assert 'private_field' not in json.dumps(projected)
    assert 'not projected' not in json.dumps(projected)
    result = selected(projected)
    assert result.text == TEXT
    assert [(a['url_citation']['ref_id'], a['url_citation']['url']) for a in result.annotations] == [
        ('turn0search0', 'https://example.org/first'), ('turn0search1', 'https://example.org/second')]
    assert selected(mapping()).annotations == result.annotations


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///tmp/test', '//example.org',
                                 'https://user:password@example.org', 'https://example.org:bad',
                                 'https://example.org/\npath', 'https://[broken', 'x' * 4001, None])
def test_invalid_links_never_break_text_or_become_annotations(url):
    node = {'citation_references': [{'url': url, 'matched_text': 'citeturn0search0'}]}
    assert annotations_for_node(node, TEXT) == []


def test_missing_ambiguous_and_unknown_references_are_not_guessed():
    refs = references()
    refs[0]['items'].append({'url': 'https://example.org/conflict', 'ref_id': 'turn0search0'})
    refs.append({'url': 'https://example.org/not-in-text', 'ref_id': 'turn9search9'})
    result = annotations_for_node({'citation_references': refs}, TEXT)
    assert [a['url_citation']['ref_id'] for a in result] == ['turn0search1']
    for value in [None, 'bad', {}, [None, 123, {'items': 'bad'}]]:
        assert annotations_for_node({'citation_references': value}, TEXT) == []
    group = references()[0]
    for item in group['items']:
        item.pop('refs')
    assert annotations_for_node({'citation_references': [group]}, TEXT) == []


def test_single_url_explicit_marker_and_plain_markdown():
    refs = [{'matched_text': 'citeturn0search0', 'items': [{'url': 'https://example.org'}]}]
    result = annotations_for_node({'citation_references': refs}, TEXT)
    assert result[0]['url_citation']['ref_id'] == 'turn0search0'
    assert annotations_for_node({'citation_references': refs}, '[link](https://example.org)') == []


@pytest.mark.asyncio
@pytest.mark.parametrize('reply_source', ['backend', 'reconciled'])
async def test_api_preserves_citations_and_does_not_leak_them_to_next_reply(monkeypatch, reply_source):
    monkeypatch.setenv('W2A_REPLY_SOURCE', reply_source)
    result = selected(project(mapping()))
    d = driver_for(monkeypatch, ['obsolete DOM text'], TEXT)
    d._fetch_text_for_turn = AsyncMock(return_value=result)
    server = APIServer(Config(), d)
    body = json.loads((await server._full_response(None, 'auto', 'request', 120)).body)
    message = body['choices'][0]['message']
    assert message == {'role': 'assistant', 'content': TEXT, 'annotations': result.annotations}
    d._fetch_text_for_turn.return_value = type(result)('matched', text='plain answer')
    message = json.loads((await server._full_response(None, 'auto', 'request', 120)).body)['choices'][0]['message']
    assert message == {'role': 'assistant', 'content': 'plain answer'}


@pytest.mark.asyncio
async def test_sse_emits_same_annotations_and_exact_text(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    monkeypatch.setenv('W2A_REPLY_SOURCE', 'backend')
    result = selected(project(mapping()))
    d = driver_for(monkeypatch, [], TEXT)
    d._fetch_text_for_turn = AsyncMock(return_value=result)
    d._js_strict = AsyncMock(return_value='{}')
    server = APIServer(Config(), d)
    server._check_circuit_or_recover = AsyncMock()
    app = web.Application()
    async def handler(request):
        return await server._stream_response(request, 'auto', 'request', 120)
    app.router.add_get('/', handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.get('/')
        events = [json.loads(line[6:]) for line in (await response.text()).splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
    deltas = [e['choices'][0]['delta'] for e in events]
    assert ''.join(delta.get('content', '') for delta in deltas) == TEXT
    assert [a for delta in deltas for a in delta.get('annotations', [])] == result.annotations
    assert events[-1]['choices'][0]['finish_reason'] == 'stop'
