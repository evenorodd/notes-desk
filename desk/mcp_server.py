"""An MCP server for the notes desk: python3 notes_desk.py mcp

MCP (Model Context Protocol) is the open standard AI assistants use to call
tools. This server speaks it over standard input and output, which is how
Claude Desktop, Claude Code and other MCP clients start local servers, and
offers the read-only tools in desk/tools.py.

It's written against the published protocol with Python's standard library
only (JSON-RPC 2.0 messages, one per line), so there's nothing to install.
Nothing but protocol messages may go to stdout; anything else goes to stderr.
"""

import json
import sys

from . import DeskError, tools

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

INSTRUCTIONS = (
    "Tools for a book of structured notes (income/autocallable, buffered, principal-protected) valued "
    "nightly against closing prices. All tools are read-only except run_nightly, when it is offered. "
    "Take every number from a tool, never work "
    "out a payout yourself: use payout_at_moves. Percentages come back in percent. 'Cushion' is how much "
    "further the underlier can fall before principal is lost at maturity; negative means already past "
    "the buffer or barrier. Cite a note's sec_filing link when explaining its terms."
)


class Server:
    def __init__(self, call_tool=tools.call, tool_list=tools.TOOLS, out=None):
        self.call_tool = call_tool
        self.tool_list = tool_list
        self.out = out or sys.stdout

    # One JSON-RPC message in, at most one out.
    def handle(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return self.error(None, -32700, "Parse error: not valid JSON")
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
                return None   # a response to something we never asked: ignore
            return self.error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")

        method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
        if "id" not in msg:   # a notification (initialized, cancelled, ...): no reply
            return None

        if method == "initialize":
            asked = params.get("protocolVersion")
            return self.result(msg_id, {
                "protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "notes-desk", "version": "1.0.0"},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return self.result(msg_id, {})
        if method == "tools/list":
            return self.result(msg_id, {"tools": [
                {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"],
                 "annotations": ({"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True,
                                  "openWorldHint": True} if t.get("writes") else
                                 {"readOnlyHint": True, "openWorldHint": False})}
                for t in self.tool_list]})
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in {t["name"] for t in self.tool_list}:
                return self.error(msg_id, -32602, f"Unknown tool: {name}")
            if not isinstance(args, dict):
                return self.error(msg_id, -32602, "arguments must be an object")
            try:
                data = self.call_tool(name, args)
            except DeskError as e:
                # A problem the assistant should see and can act on (wrong CUSIP, bad date, ...).
                text = str(e) + "".join(f"\n  {p}" for p in e.problems)
                return self.result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})
            except Exception as e:   # noqa: BLE001 - report, don't crash the server
                print(f"[notes-desk mcp] {name} failed: {type(e).__name__}: {e}", file=sys.stderr)
                return self.result(msg_id, {"content": [{"type": "text", "text":
                                   f"The tool failed: {type(e).__name__}: {e}"}], "isError": True})
            return self.result(msg_id, {"content": [{"type": "text", "text": json.dumps(data, indent=1)}],
                                        "structuredContent": data, "isError": False})
        return self.error(msg_id, -32601, f"Method not found: {method}")

    @staticmethod
    def result(msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def error(msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def serve(self, stdin=None):
        stdin = stdin or sys.stdin
        for line in stdin:
            if not line.strip():
                continue
            reply = self.handle(line)
            if reply is not None:
                self.out.write(json.dumps(reply) + "\n")
                self.out.flush()


def main(allow_runs=False):
    # Keep stdout for protocol messages only: anything else printed while a
    # tool runs goes to stderr, where MCP clients show it as a log.
    out = sys.stdout
    sys.stdout = sys.stderr
    kind = "read-only tools plus run_nightly" if allow_runs else "read-only tools"
    print(f"notes-desk MCP server ready ({kind}; reading requests on stdin)", file=sys.stderr)
    try:
        Server(tool_list=tools.available(allow_runs), out=out).serve(sys.stdin)
    except KeyboardInterrupt:
        pass
