"""Conservative final-text fallback for a fresh, browser-only WEB: conversation."""
from urllib.parse import unquote, urlsplit

from .turn_anchor import normalize_text


WEB_REPLY_SNAPSHOT_JS = r"""
(() => {
    const users = document.querySelectorAll('[data-message-author-role="user"]');
    const assistants = document.querySelectorAll('[data-message-author-role="assistant"]');
    const info = {url: location.href, user_count: users.length, assistant_count: assistants.length};
    if (users.length !== 1 || assistants.length !== 1) return JSON.stringify(info);
    const user = users[0], answer = assistants[0];
    if (!(user.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING)) return JSON.stringify(info);
    const scope = answer.closest('article') || answer.closest('[data-testid^="conversation-turn-"]');
    const visible = el => !!el && el.getClientRects().length > 0;
    const action = scope && [...scope.querySelectorAll(
        '[data-testid="copy-turn-action-button"], [data-testid="good-response-turn-action-button"], [data-testid="bad-response-turn-action-button"]'
    )].some(visible);
    const stopped = ![...document.querySelectorAll('[data-testid="stop-button"]')].some(visible);
    const markdown = [...answer.querySelectorAll('.markdown')];
    info.user_text = user.innerText || user.textContent || '';
    const blocks = markdown.flatMap(el => [...el.querySelectorAll('pre')]);
    if (blocks.length) {
        // Only accept an isolated code block. Mixed prose/code needs the
        // backend's original text; rendered labels are not model output.
        if (markdown.length !== 1 || blocks.length !== 1) return JSON.stringify(info);
        const code = blocks[0].querySelector('code');
        if (!code) return JSON.stringify(info);
        const outside = markdown[0].cloneNode(true);
        outside.querySelectorAll('pre, button, [role="button"]').forEach(el => el.remove());
        if (outside.textContent.trim()) return JSON.stringify(info);
        info.text = code.textContent;
    } else {
        info.text = markdown.map(el => el.innerText || el.textContent || '').join('\n');
    }
    info.complete = !!action && stopped && visible(answer);
    return JSON.stringify(info);
})()
"""


def confirmed_web_reply(snapshot: dict, sent_text: str) -> str:
    """Reject old turns, partial output and unrelated pages rather than guess."""
    if not isinstance(snapshot, dict):
        return ""
    url = urlsplit(str(snapshot.get("url", "")))
    if url.hostname != "chatgpt.com" or "/c/" not in url.path:
        return ""
    conversation_id = unquote(url.path.split("/c/", 1)[1].split("/")[0])
    if not conversation_id.startswith("WEB:"):
        return ""
    if snapshot.get("user_count") != 1 or snapshot.get("assistant_count") != 1:
        return ""
    if snapshot.get("complete") is not True:
        return ""
    user_text, answer = snapshot.get("user_text"), snapshot.get("text")
    if not isinstance(user_text, str) or not isinstance(answer, str):
        return ""
    if not sent_text or normalize_text(user_text) != normalize_text(sent_text):
        return ""
    return answer if answer.strip() else ""
