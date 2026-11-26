"""Tests for the MCP server's protocol handling and argument checks (no database)."""

import io
import json
import unittest

from desk import DeskError
from desk.mcp_server import Server
from desk.tools import TOOLS, available, check_arguments


def fake_call(name, args):
    if args.get("cusip") == "BAD":
        raise DeskError("No note with CUSIP BAD.")
    return {"tool": name, "args": args}


def ask(server, method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(json.dumps(msg))


class Protocol(unittest.TestCase):
    def setUp(self):
        self.s = Server(call_tool=fake_call)

    def test_initialize_agrees_a_version(self):
        r = ask(self.s, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "t", "version": "1"}})["result"]
        self.assertEqual(r["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["capabilities"])
        r = ask(self.s, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")

    def test_notifications_get_no_reply(self):
        self.assertIsNone(self.s.handle('{"jsonrpc":"2.0","method":"notifications/initialized"}'))

    def test_tools_list(self):
        names = [t["name"] for t in ask(self.s, "tools/list")["result"]["tools"]]
        self.assertEqual(names, [t["name"] for t in TOOLS])
        for t in ask(self.s, "tools/list")["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["annotations"]["readOnlyHint"])

    def test_tool_call_result_and_tool_error(self):
        r = ask(self.s, "tools/call", {"name": "get_note", "arguments": {"cusip": "46661MLN2"}})["result"]
        self.assertFalse(r["isError"])
        self.assertEqual(json.loads(r["content"][0]["text"])["args"]["cusip"], "46661MLN2")
        r = ask(self.s, "tools/call", {"name": "get_note", "arguments": {"cusip": "BAD"}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("No note", r["content"][0]["text"])

    def test_protocol_errors(self):
        self.assertEqual(ask(self.s, "tools/call", {"name": "drop_tables"})["error"]["code"], -32602)
        self.assertEqual(ask(self.s, "no/such/method")["error"]["code"], -32601)
        self.assertEqual(self.s.handle("not json")["error"]["code"], -32700)

    def test_serve_writes_one_line_per_reply(self):
        out = io.StringIO()
        Server(call_tool=fake_call, out=out).serve(io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'))
        lines = out.getvalue().splitlines()
        self.assertEqual([json.loads(l)["id"] for l in lines], [1, 2])


class RunTool(unittest.TestCase):
    def names(self, allow_runs):
        s = Server(call_tool=fake_call, tool_list=available(allow_runs))
        return {t["name"]: t for t in ask(s, "tools/list")["result"]["tools"]}

    def test_hidden_unless_allowed(self):
        self.assertNotIn("run_nightly", self.names(False))
        s = Server(call_tool=fake_call, tool_list=available(False))
        r = ask(s, "tools/call", {"name": "run_nightly", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_offered_and_marked_as_writing_when_allowed(self):
        tools = self.names(True)
        self.assertFalse(tools["run_nightly"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["book_summary"]["annotations"]["readOnlyHint"])


class Arguments(unittest.TestCase):
    schema = next(t for t in TOOLS if t["name"] == "payout_at_moves")["input_schema"]

    def test_valid(self):
        self.assertEqual(check_arguments(self.schema, {"cusip": "X", "moves_pct": [-10, 5.5]}), [])

    def test_problems(self):
        self.assertIn("missing required argument 'moves_pct'", check_arguments(self.schema, {"cusip": "X"}))
        self.assertIn("unknown argument 'sql'", check_arguments(self.schema, {"cusip": "X", "moves_pct": [1], "sql": "x"}))
        self.assertIn("'moves_pct' should be a list of numbers",
                      check_arguments(self.schema, {"cusip": "X", "moves_pct": ["ten"]}))
        find = next(t for t in TOOLS if t["name"] == "find_notes")["input_schema"]
        self.assertTrue(check_arguments(find, {"note_type": "swap"}))
        self.assertTrue(check_arguments(find, {"limit": True}))


if __name__ == "__main__":
    unittest.main()
