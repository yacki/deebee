"""Independent MCP smoke client. Receives secrets only through environment variables.

DEEBEE_MCP_URL=https://host/deebee/mcp/
DEEBEE_API_KEY=... or DEEBEE_ACCESS_TOKEN=...
DEEBEE_IDENTITY_SOURCE=... (required for external keys / opaque tokens)
"""
import argparse
import asyncio
import json
import os
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    url = os.environ["DEEBEE_MCP_URL"]
    key, token = os.getenv("DEEBEE_API_KEY", ""), os.getenv("DEEBEE_ACCESS_TOKEN", "")
    if bool(key) == bool(token):
        raise SystemExit("Set exactly one of DEEBEE_API_KEY / DEEBEE_ACCESS_TOKEN")
    headers = {"X-DeeBee-API-Key": key} if key else {"Authorization": "Bearer " + token}
    if os.getenv("DEEBEE_IDENTITY_SOURCE"):
        headers["X-DeeBee-Identity-Source"] = os.environ["DEEBEE_IDENTITY_SOURCE"]
    async with httpx.AsyncClient(headers=headers, timeout=60, trust_env=False) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                print("Connected:", initialized.serverInfo.name, initialized.protocolVersion)
                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    if result.isError:
                        raise RuntimeError(json.dumps(result.structuredContent, ensure_ascii=False))
                    return result.structuredContent
                print(json.dumps(await call("identity.me", {}), ensure_ascii=False))
                tools = await session.list_tools()
                print("Tools:", ", ".join(t.name for t in tools.tools))
                cursor, selected = "", None
                while True:
                    page = await call("resources.list", {"type": args.protocol, "cursor": cursor})
                    selected = next((r for r in page["resources"] if not args.resource_id or r["id"] == args.resource_id), None)
                    if selected or not page.get("next_cursor"):
                        break
                    cursor = page["next_cursor"]
                if not selected:
                    raise SystemExit("No permitted resource matches the request")
                tool = "ssh.exec" if args.protocol == "ssh" else "db.query"
                payload = {"resource_id": selected["id"], "mode": "normal", "idempotency_key": str(uuid.uuid4())}
                payload.update({"command": "id -un"} if args.protocol == "ssh" else {"sql": "SELECT 1 AS ready"})
                submitted = await call(tool, payload)
                for _ in range(120):
                    result = await call("executions.get", {"execution_id": submitted["execution_id"]})
                    if result["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                        if result["status"] != "succeeded":
                            raise SystemExit("Not successful; no automatic replay performed")
                        return
                    await asyncio.sleep(.5)
                raise SystemExit("Polling stopped; inspect execution_id without resubmitting: " + submitted["execution_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", choices=["ssh", "mysql", "postgresql"], default="ssh")
    parser.add_argument("--resource-id", default="")
    asyncio.run(run(parser.parse_args()))
