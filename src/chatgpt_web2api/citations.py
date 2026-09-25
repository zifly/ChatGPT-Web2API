"""Resolve web citations from the selected reply only, without guessing URLs."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

_MARKER = re.compile(r"\ue200cite\ue202([^\ue201\r\n]+)\ue201")
_REF = re.compile(r"turn\d+[A-Za-z]+\d+\Z")

# Executed inside the conversation projection. Whitelist small reference fields;
# do not return snippets, search queries, assets or arbitrary message metadata.
CITATION_PROJECTION_JS = r"""
function projectCitations(metadata) {
  var budget = 100;
  function list(value) { return Array.isArray(value) ? value.slice(0, 100) : []; }
  function string(value, limit) { return typeof value === 'string' ? value.slice(0, limit) : ''; }
  function ref(value) {
    if (typeof value === 'string') return string(value, 200);
    if (!value || typeof value !== 'object') return null;
    return {turn_index: Number.isSafeInteger(value.turn_index) ? value.turn_index : null,
      ref_type: string(value.ref_type, 30),
      ref_index: Number.isSafeInteger(value.ref_index) ? value.ref_index : null};
  }
  function source(value) {
    value = value && typeof value === 'object' ? value : {};
    return {url: string(value.url, 4001), title: string(value.title, 500),
      ref_id: string(value.ref_id, 200), id: string(value.id, 200),
      refs: list(value.refs).map(ref), matched_text: string(value.matched_text, 2000)};
  }
  return list(metadata && metadata.content_references).map(function(value) {
    var result = source(value);
    result.items = list(value && value.items).slice(0, budget).map(source);
    budget -= result.items.length;
    return result;
  });
}
""".strip()


def _list(value):
    return value[:100] if isinstance(value, list) else []


def _refs(source: dict) -> set[str]:
    result = set()
    for value in [source.get('ref_id'), source.get('id'), *_list(source.get('refs'))]:
        if isinstance(value, dict):
            turn, kind, index = value.get('turn_index'), value.get('ref_type'), value.get('ref_index')
            if type(turn) is int and type(index) is int and turn >= 0 and index >= 0 and isinstance(kind, str):
                value = f'turn{turn}{kind}{index}'
        if isinstance(value, str) and _REF.fullmatch(value):
            result.add(value)
    return result


def _markers(text: str) -> set[str]:
    return {ref for match in _MARKER.finditer(text) for ref in match[1].split('\ue202')
            if _REF.fullmatch(ref)}


def annotations_for_node(node: dict, text: str) -> list[dict]:
    """Return explicit, unambiguous HTTP(S) sources for markers in this text.

    Missing/unsupported metadata leaves the original marker intact. A grouped
    marker alone cannot pair multiple URLs with multiple reference IDs.
    """
    wanted = _markers(text)
    if not wanted:
        return []
    references = node.get('citation_references')
    if references is None:
        metadata = (node.get('message') or {}).get('metadata') or {}
        references = metadata.get('content_references') if isinstance(metadata, dict) else []
    found: dict[str, dict[str, str]] = {}
    for group in _list(references):
        if not isinstance(group, dict):
            continue
        items = [item for item in _list(group.get('items')) if isinstance(item, dict)]
        for source in [group, *items]:
            url = source.get('url')
            if not isinstance(url, str) or len(url) > 4000 or any(c.isspace() or ord(c) < 32 for c in url):
                continue
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
                    continue
                parsed.port  # Reject malformed ports as well.
            except ValueError:
                continue
            refs = _refs(source)
            if not refs:
                marker = source.get('matched_text', '')
                refs = _markers(marker) if isinstance(marker, str) else set()
                # A single explicit URL can resolve a single marker. Never zip
                # a group's marker IDs with its URLs by array position.
                if len(refs) != 1:
                    refs = set()
                if not refs and len(items) == 1 and source is items[0]:
                    marker = group.get('matched_text', '')
                    refs = _markers(marker) if isinstance(marker, str) else set()
                    if len(refs) != 1:
                        refs = set()
            title = source.get('title')
            title = title[:500] if isinstance(title, str) and title else parsed.hostname
            for ref in refs & wanted:
                found.setdefault(ref, {})[url] = title
    result = []
    # Preserve order of first occurrence in the unchanged reply text.
    for match in _MARKER.finditer(text):
        for ref in match[1].split('\ue202'):
            urls = found.pop(ref, {})
            if len(urls) == 1:
                url, title = next(iter(urls.items()))
                result.append({'type': 'url_citation', 'url_citation': {
                    'ref_id': ref, 'url': url, 'title': title,
                    'start_index': match.start(), 'end_index': match.end(),
                }})
            if len(result) == 100:
                return result
    return result
