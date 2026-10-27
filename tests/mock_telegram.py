"""A fake Telegram Bot API server for testing bots without a real token.

A conversation script decides what the user sends next, based on the bot's
replies so far. The bot runs with PLAIN_BOT_EXIT_WHEN_IDLE=1, so it stops when
the script has nothing more to say.
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "123:TEST"


class MockTelegram:
    def __init__(self, script, user_name="Ana", chat_id=42):
        self.script = script          # callable(replies) -> next message text or None
        self.user_name = user_name
        self.chat_id = chat_id
        self.replies = []             # (text, buttons) in order
        self.sent = []                # messages the user sent
        self.update_id = 100
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                if not self.path.startswith(f"/bot{TOKEN}/"):
                    return self.answer(401, {"ok": False, "description": "Unauthorized"})
                if method == "getUpdates":
                    return self.answer(200, {"ok": True, "result": mock.updates()})
                if method == "sendMessage":
                    keyboard = body.get("reply_markup", {}).get("keyboard", [])
                    mock.replies.append((body["text"], [b["text"] for row in keyboard for b in row]))
                    return self.answer(200, {"ok": True, "result": {"message_id": len(mock.replies)}})
                self.answer(404, {"ok": False, "description": "Not Found"})

            def answer(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def updates(self):
        text = self.script(self.replies)
        if text is None:
            return []
        self.sent.append(text)
        self.update_id += 1
        return [{"update_id": self.update_id,
                 "message": {"message_id": self.update_id, "date": 0, "text": text,
                             "chat": {"id": self.chat_id, "type": "private"},
                             "from": {"id": self.chat_id, "is_bot": False, "first_name": self.user_name}}}]

    def run(self, command, timeout=60):
        """Run a bot process against this server and return the replies it sent."""
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        env = dict(os.environ, TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_API_URL=self.url,
                   PLAIN_BOT_EXIT_WHEN_IDLE="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        try:
            proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
        finally:
            self.server.shutdown()
            self.server.server_close()
        if proc.returncode != 0:
            raise AssertionError(f"bot exited with {proc.returncode}:\n{proc.stderr}")
        return self.replies


def say(*messages):
    """A script that sends fixed messages one by one, each after the previous reply."""
    messages = list(messages)

    def script(replies):
        return messages.pop(0) if messages else None
    return script
