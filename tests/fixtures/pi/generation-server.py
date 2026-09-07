#!/usr/bin/env python3
"""Scripted OpenAI-compatible chat-completions server for generating REAL pi
session files. pi (the real writer) talks to this; every request body is logged
to requests.jsonl so scripted tool-call arguments can be matched to pi's real
tool schemas. The session bytes on disk are written by pi itself.

Scripting: the response is chosen by how many 'tool' role messages appear in
the request, so one server process drives a deterministic multi-turn session:
  step 0 (no tool results yet): reasoning + narration + TWO tool calls
  step 1 (2 tool results):      narration + one more tool call (write)
  step 2 (3 tool results):      final text, finish_reason=stop
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LOG = Path(__file__).parent / "requests.jsonl"
PORT = 8138
SCENARIO = os.environ.get("SCENARIO", "rich")


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(delta, finish=None):
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "mock-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def tool_call_chunks(index, call_id, name, args):
    yield chunk(
        {
            "tool_calls": [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": ""},
                }
            ]
        }
    )
    yield chunk({"tool_calls": [{"index": index, "function": {"arguments": json.dumps(args)}}]})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("models"):
            body = json.dumps(
                {
                    "object": "list",
                    "data": [{"id": "mock-model", "object": "model", "owned_by": "mock"}],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # noqa: C901 -- scripted scenario dispatch, fixture artefact
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            req = {"unparseable": raw.decode(errors="replace")}
        with LOG.open("a") as f:
            f.write(json.dumps({"path": self.path, "body": req}) + "\n")

        msgs = req.get("messages", [])
        tool_results = sum(1 for m in msgs if m.get("role") == "tool")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def w(c):
            self.wfile.write(sse(c))
            self.wfile.flush()

        if SCENARIO == "error":
            if tool_results == 0:
                w(chunk({"role": "assistant"}))
                w(chunk({"content": "I'll read the config file."}))
                for c in tool_call_chunks(
                    0, "call_mock_fail", "read", {"path": "does-not-exist.txt"}
                ):
                    w(c)
                w(chunk({}, finish="tool_calls"))
            else:
                w(chunk({"role": "assistant"}))
                w(chunk({"content": "The file is missing, so I'll stop here."}))
                w(chunk({}, finish="stop"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        if tool_results == 0:
            w(chunk({"role": "assistant"}))
            w(
                chunk(
                    {
                        "reasoning_content": "The user wants a workspace summary. "
                        "Plan: list the directory, read notes.txt, then write summary.txt."
                    }
                )
            )
            w(chunk({"content": "I'll list the workspace and read the notes file first."}))
            for c in tool_call_chunks(0, "call_mock_ls", "bash", {"command": "ls -1"}):
                w(c)
            for c in tool_call_chunks(1, "call_mock_read", "read", {"path": "notes.txt"}):
                w(c)
            w(chunk({}, finish="tool_calls"))
        elif tool_results == 2:
            w(chunk({"role": "assistant"}))
            w(chunk({"content": "Now I'll write the summary file."}))
            for c in tool_call_chunks(
                0,
                "call_mock_write",
                "write",
                {
                    "path": "summary.txt",
                    "content": "Workspace has notes.txt: remember the meeting.",
                },
            ):
                w(c)
            w(chunk({}, finish="tool_calls"))
        else:
            w(chunk({"role": "assistant"}))
            w(
                chunk(
                    {
                        "content": "Done. The workspace contains notes.txt and now "
                        "summary.txt with a one-line summary."
                    }
                )
            )
            w(chunk({}, finish="stop"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
