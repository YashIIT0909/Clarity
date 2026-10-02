"""Run one agent-check message through Claude Code headless and print what matters: each tool call, exactly what
the model received back, and its final answer. Run it from the directory where the `clarity` MCP server is
registered (`claude mcp list` shows it there):
  python3 Clarity/backend/clarity_mcp/tests/agent_transcript.py "My friend sent me this AirPods deal ..."
"""
import json
import subprocess
import sys

TOOL = "mcp__clarity__clarity_check_listing"

message = " ".join(sys.argv[1:])
if not message:
    sys.exit(__doc__)
out = subprocess.run(["claude", "-p", message, "--allowedTools", TOOL, "--output-format", "stream-json", "--verbose"],
                     capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600).stdout

print(f"USER: {message}\n")
for line in out.splitlines():
    try:
        e = json.loads(line)
    except ValueError:
        continue
    if e.get("type") == "system" and e.get("subtype") == "init":
        print("clarity tools available:", [t for t in e.get("tools", []) if "clarity" in t], "\n")
    if e.get("type") not in ("user", "assistant"):
        continue
    for c in e["message"].get("content") or []:
        if c.get("type") == "tool_use":
            print(f"TOOL CALL: {c['name']} {json.dumps(c['input'], ensure_ascii=False)}")
        elif c.get("type") == "tool_result":
            body = c["content"] if isinstance(c["content"], str) else json.dumps(c["content"], ensure_ascii=False)
            print(f"MODEL RECEIVED ({len(body)} chars): {body}\n")
        elif c.get("type") == "text" and e["type"] == "assistant":
            print(f"ASSISTANT: {c['text']}\n")
