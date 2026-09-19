"""Isolated CLI regression tests; no running Chrome or account is needed."""
import ast
import asyncio
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class CookieImportTests(unittest.TestCase):
    def run_import(self, auth_value, reject=False, exception=False):
        # Load only the CLI function: the full module imports the API service.
        source = Path(__file__).parents[1] / "src/chatgpt_web2api/__main__.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "inject_cookies")
        config = SimpleNamespace(chrome=SimpleNamespace(cdp_port=9222))
        namespace = {
            "asyncio": asyncio, "json": json, "os": __import__("os"), "sys": sys,
            "Config": SimpleNamespace(load=lambda _: config),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        sent = []
        replies = []

        async def send(payload):
            request = json.loads(payload)
            sent.append(request)
            if request["method"] == "Network.setCookie":
                result = {"success": not reject}
            elif request["method"] == "Runtime.evaluate":
                result = {"result": {"value": auth_value}}
                if exception:
                    result["exceptionDetails"] = {"text": "JS failed"}
            else:
                result = {}
            # Unrelated CDP events must not be mistaken for replies.
            replies.extend([json.dumps({"method": "Page.event"}),
                            json.dumps({"id": request["id"], "result": result})])

        async def recv():
            return replies.pop(0)

        ws = SimpleNamespace(send=send, recv=recv, close=AsyncMock())
        browser = SimpleNamespace(connect=AsyncMock(return_value=ws))
        targets = [{"type": "page", "webSocketDebuggerUrl": "ws://localhost/test"}]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / "cookies.json"
            cookie_file.write_text(json.dumps([
                {"name": "test", "value": "secret", "sameSite": s}
                for s in ["lax", "strict", "no_restriction", "unspecified"]
            ]), encoding="utf-8-sig")
            args = SimpleNamespace(config=None, cdp_port=None, user_data_dir=None,
                                   cookies=str(cookie_file))
            with patch.dict(sys.modules, {"websockets": browser}), \
                 patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(targets).encode())), \
                 patch("asyncio.sleep", new=AsyncMock()), contextlib.redirect_stdout(output):
                namespace["inject_cookies"](args)
        return sent, output.getvalue()

    def test_object_result_does_not_crash_or_leak(self):
        _, output = self.run_import({"private": "do-not-print"})
        self.assertIn("unexpected browser result", output)
        self.assertNotIn("do-not-print", output)

    def test_export_format_and_success(self):
        sent, output = self.run_import("OK")
        cookies = [r["params"] for r in sent if r["method"] == "Network.setCookie"]
        self.assertEqual([c.get("sameSite") for c in cookies], ["Lax", "Strict", "None", None])
        self.assertIn("Auth verified", output)
        self.assertEqual(sent[4]["method"], "Page.navigate")

    def test_rejected_cookie_is_not_reported_as_imported(self):
        with self.assertRaisesRegex(RuntimeError, "Chrome rejected cookie 1"):
            self.run_import("OK", reject=True)

    def test_js_exception_is_not_success(self):
        _, output = self.run_import("OK", exception=True)
        self.assertIn("browser evaluation failed", output)
        self.assertNotIn("Auth verified", output)

    def test_http_failure_is_reported(self):
        _, output = self.run_import("HTTP_403")
        self.assertIn("HTTP_403", output)
        self.assertNotIn("Auth verified", output)


if __name__ == "__main__":
    unittest.main()
