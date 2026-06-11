"""AnySearch web search plugin — bundled, auto-loaded.

AnySearch provides a free, no-auth MCP endpoint at api.anysearch.com/mcp
that supports both web search and content extraction. It works without
VPN from mainland China, making it a viable alternative to DuckDuckGo
which is blocked in that region.
"""

from plugins.web.anysearch.provider import AnySearchWebSearchProvider


def register(plugin_context):
    ctx = plugin_context
    ctx.register_web_search_provider(AnySearchWebSearchProvider())