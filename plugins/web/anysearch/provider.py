"""AnySearch web provider — plugin form (via free MCP endpoint).

AnySearch provides a free, no-auth web search + extract API via MCP
at api.anysearch.com/mcp. It works without VPN from mainland China,
unlike DuckDuckGo which is blocked in that region.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List
from urllib.request import Request, urlopen

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

ANYSEARCH_MCP_URL = "https://api.anysearch.com/mcp"


class AnySearchWebSearchProvider(WebSearchProvider):
    """AnySearch MCP-based web search and extract provider.

    No API key needed. Uses the AnySearch MCP HTTP endpoint with
    JSON-RPC 2.0 protocol. Supports both search and extract.
    """

    @property
    def name(self) -> str:
        return "anysearch"

    @property
    def display_name(self) -> str:
        return "AnySearch (anysearch)"

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit), 100))
        try:
            texts = _mcp_tool_call("search", {
                "query": query,
                "limit": safe_limit,
            })
            web_results = _parse_search_text(texts)
            logger.info(
                "AnySearch search '%s': %d results (limit %d)",
                query, len(web_results), limit,
            )
            return {"success": True, "data": {"web": web_results}}
        except Exception as exc:
            logger.warning("AnySearch search error: %s", exc)
            return {"success": False, "error": f"AnySearch search failed: {exc}"}

    def extract(self, url: str) -> Dict[str, Any]:
        if not url:
            return {"success": False, "error": "URL is required for extract"}
        try:
            result = _mcp_tool_call("extract", {"url": url})
            extracted = _merge_extract_result(result)
            return {"success": True, "data": {"content": extracted}}
        except Exception as exc:
            logger.warning("AnySearch extract error: %s", exc)
            return {"success": False, "error": f"AnySearch extract failed: {exc}"}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "AnySearch (anysearch)",
            "badge": "free · no key · search + extract · China-friendly",
            "tag": "Search via AnySearch MCP — free, no API key, works in mainland China",
            "env_vars": [],
        }


def _mcp_tool_call(tool_name: str, arguments: Dict[str, Any]) -> List[str]:
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
        "id": _request_id(tool_name, arguments),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        ANYSEARCH_MCP_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=30) as resp:
        response = json.loads(resp.read().decode("utf-8"))
    if "error" in response:
        err = response["error"]
        raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
    result = response.get("result", {})
    return [block.get("text", "") for block in result.get("content", []) if block.get("text")]


def _request_id(tool_name: str, args: Dict[str, Any]) -> int:
    raw = f"{tool_name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    h = hashlib.md5(raw.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


_URL_PAT = re.compile(r"\*\*URL\*\*:\s*(https?://\S+)")
_TITLE_PAT = re.compile(r"^###\s+\d+\.\s+(.+)$")


def _parse_search_text(texts: List[str]) -> list:
    results = []
    for text in texts:
        entries = text.split("\n\n### ")
        for i, entry in enumerate(entries):
            if i > 0:
                entry = "### " + entry
            lines = entry.strip().split("\n")
            item = {}
            for line in lines:
                line = line.strip()
                title_m = _TITLE_PAT.match(line)
                if title_m:
                    item["title"] = title_m.group(1).strip()
                    continue
                url_m = _URL_PAT.search(line)
                if url_m:
                    item["url"] = url_m.group(1).strip()
                    continue
                if line.startswith("- ") and "url" not in item.get("url", ""):
                    desc = line[2:].strip()
                    if desc and not desc.startswith("**URL**") and "description" not in item:
                        item["description"] = desc
            if item.get("url"):
                item.setdefault("title", "")
                item.setdefault("description", "")
                results.append(item)
    return results


def _merge_extract_result(result: List[str]) -> str:
    parts = []
    for item in result:
        if isinstance(item, dict):
            parts.append(str(item.get("content", item.get("text", ""))))
        else:
            parts.append(str(item))
    return "\n\n".join(filter(None, parts))