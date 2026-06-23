"""Router pattern: two-layer intent classification with keyword + LLM fallback.

Routes user queries to one of three handlers based on intent:
  - github_search  → GitHub Search API
  - knowledge_query → Local knowledge base
  - general_chat    → LLM direct answer

Architecture:
  Layer 1: Keyword fast-match (zero-cost, no LLM call)
  Layer 2: LLM classification (fallback for ambiguous queries)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pipeline.model_client import (
    Usage,
    chat_with_retry,
    quick_chat,
)

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_SEARCH_API = "https://api.github.com/search/repositories"
KNOWLEDGE_ARTICLES_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "articles"
INTENT_CLASSIFICATION_PROMPT = """You are an intent classifier. Given a user query,
output EXACTLY one of these JSON objects with no extra text:

- {{"intent": "github_search"}}  for queries about finding GitHub repositories or open-source projects
- {{"intent": "knowledge_query"}} for queries about AI/LLM/Agent knowledge or tech articles
- {{"intent": "general_chat"}}    for general conversation or questions

User query: {query}
"""

GITHUB_SEARCH_QUERY_PROMPT = """Extract the best GitHub search keywords from the user query.
Output ONLY a JSON object with the key "keywords" containing a space-separated string
of English technical keywords (translate if needed, 3-5 words max). No extra text.

User query: {query}
"""

# Layer 1: keyword-based fast matching.
# Each tuple is (keywords_list, intent_name).
# Matched case-insensitively against the lowercased query.
_KEYWORD_RULES: Sequence[tuple[Sequence[str], str]] = [
    (
        [
            "github",
            "repository",
            "repo",
            "open source",
            "开源",
            "仓库",
            "star",
            "github trending",
        ],
        "github_search",
    ),
    (
        [
            "knowledge",
            "知识",
            "article",
            "文章",
            "知识库",
            "digest",
            "摘要",
            "ai 动态",
            "llm 动态",
            "agent 相关",
        ],
        "knowledge_query",
    ),
]

# ---------------------------------------------------------------------------
# LLM wrappers — adapt pipeline.model_client to expected (text, usage) API
# ---------------------------------------------------------------------------


def chat(
    messages: Sequence[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> tuple[str, Usage]:
    """Send chat messages and return (text, usage) tuple.

    Thin wrapper over ``chat_with_retry`` that unwraps ``LLMResponse``
    into the expected ``(text, usage)`` contract.

    Args:
        messages: OpenAI-style chat messages.
        model: Optional model override.
        temperature: Sampling temperature.
        max_tokens: Optional completion token cap.

    Returns:
        Tuple of (assistant_text, token_usage).
    """
    response = chat_with_retry(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.content, response.usage


def chat_json(
    prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
) -> tuple[dict[str, Any], Usage]:
    """Send a prompt and return the JSON-parsed response.

    Used for structured outputs like intent classification.

    Args:
        prompt: User prompt requesting JSON output.
        model: Optional model override.
        temperature: Sampling temperature (lowered for structured output).

    Returns:
        Tuple of (parsed_json_dict, token_usage).
    """
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a JSON-only assistant. Output ONLY valid JSON. "
                "Do not wrap in markdown code blocks. Do not add commentary."
            ),
        },
        {"role": "user", "content": prompt},
    ]
    text, usage = chat(messages=messages, model=model, temperature=temperature)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise TypeError(f"chat_json expected a JSON object, got {type(parsed).__name__}")
    return parsed, usage


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


def classify_intent(query: str) -> str:
    """Two-layer intent classification.

    Layer 1: Fast keyword matching (zero-cost).
    Layer 2: LLM classification for ambiguous queries.

    Args:
        query: Raw user input.

    Returns:
        One of ``github_search``, ``knowledge_query``, or ``general_chat``.
    """
    # Layer 1 — keyword matching
    intent = _keyword_match(query)
    if intent is not None:
        LOGGER.debug("Layer 1 matched intent=%s for query=%r", intent, query[:80])
        return intent

    # Layer 2 — LLM classification
    LOGGER.debug("Layer 1 miss, falling back to LLM for query=%r", query[:80])
    return _classify_with_llm(query)


def _keyword_match(query: str) -> str | None:
    """Try to classify intent using keyword rules.

    Args:
        query: Raw user input (case-insensitive matching applied).

    Returns:
        Intent string if matched, or ``None`` if no rule fires.
    """
    lower = query.lower()
    for keywords, intent in _KEYWORD_RULES:
        if any(kw in lower for kw in keywords):
            return intent
    return None


def _classify_with_llm(query: str) -> str:
    """Classify intent using the LLM (Layer 2 fallback).

    Args:
        query: Raw user input.

    Returns:
        Intent string. Defaults to ``general_chat`` on parsing failure.
    """
    prompt = INTENT_CLASSIFICATION_PROMPT.format(query=query)
    try:
        result, _usage = chat_json(prompt=prompt, temperature=0.0)
        intent = result.get("intent", "general_chat")
        valid = {"github_search", "knowledge_query", "general_chat"}
        if intent not in valid:
            LOGGER.warning("LLM returned unknown intent=%r, defaulting", intent)
            intent = "general_chat"
        return intent
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        LOGGER.warning("LLM classification failed: %s, defaulting to general_chat", exc)
        return "general_chat"


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------


def _extract_search_keywords(query: str) -> str:
    """Use LLM to extract GitHub-friendly keywords from a natural language query.

    Translates Chinese queries into English technical keywords suitable
    for the GitHub Search API.

    Args:
        query: Raw user query, potentially in Chinese.

    Returns:
        Space-separated English keywords. Falls back to the raw query on failure.
    """
    prompt = GITHUB_SEARCH_QUERY_PROMPT.format(query=query)
    try:
        result, _usage = chat_json(prompt=prompt, temperature=0.0)
        keywords = result.get("keywords", "").strip()
        return keywords or query
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        LOGGER.warning("Search keyword extraction failed: %s", exc)
        return query


def _handle_github_search(query: str) -> str:
    """Search GitHub repositories via the GitHub Search API.

    The query parameter is URL-encoded via ``urllib.parse.quote`` to handle
    Chinese characters and whitespace correctly.

    Args:
        query: GitHub search query string.

    Returns:
        Formatted search results as a markdown string.
    """
    encoded = urllib.parse.quote(_extract_search_keywords(query), safe="")
    url = f"{GITHUB_SEARCH_API}?q={encoded}&sort=stars&order=desc&per_page=5"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        LOGGER.error("GitHub search failed: %s", exc)
        return f"GitHub 搜索失败: {exc}"

    items = data.get("items", [])
    if not items:
        return f"未找到与 '{query}' 相关的 GitHub 仓库。"

    lines = [f"### GitHub 搜索结果: {query}", ""]
    for i, repo in enumerate(items, 1):
        full_name = repo.get("full_name", "unknown")
        description = repo.get("description", "") or "无描述"
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language") or "Unknown"
        html_url = repo.get("html_url", "")
        lines.append(f"**{i}. [{full_name}]({html_url})** ⭐ {stars}")
        lines.append(f"   {description}")
        lines.append(f"   语言: {language}")
        lines.append("")
    return "\n".join(lines).strip()


def _handle_knowledge_query(query: str) -> str:
    """Search the local knowledge base for matching articles.

    Scans all ``.json`` files in ``knowledge/articles/`` and performs
    case-insensitive substring matching on title, summary, and tags.

    Args:
        query: Search keywords.

    Returns:
        Formatted matching articles as a markdown string.
    """
    if not KNOWLEDGE_ARTICLES_DIR.is_dir():
        return "知识库目录不存在，请先运行采集流程。"

    keywords = query.lower().split()
    matched: list[dict[str, Any]] = []

    for article_file in sorted(KNOWLEDGE_ARTICLES_DIR.glob("*.json")):
        try:
            article = json.loads(article_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Build searchable text from title, summary, tags
        search_text = (
            f"{article.get('title', '')} "
            f"{article.get('summary', '')} "
            f"{' '.join(article.get('tags', []))}"
        ).lower()

        # Simple scoring: count keyword matches
        score = sum(1 for kw in keywords if kw in search_text)
        if score > 0:
            matched.append({"article": article, "score": score})

    if not matched:
        return f"知识库中未找到与 '{query}' 相关的文章。"

    # Sort by relevance score descending, then by title
    matched.sort(key=lambda m: (-m["score"], m["article"].get("title", "")))
    top = matched[:5]

    lines = [f"### 知识库搜索结果: {query}", ""]
    for i, entry in enumerate(top, 1):
        a = entry["article"]
        title = a.get("title", "无标题")
        source_url = a.get("source_url", "")
        summary = a.get("summary", "无摘要")[:200]
        tags = ", ".join(a.get("tags", []))
        lines.append(f"**{i}. [{title}]({source_url})**" if source_url else f"**{i}. {title}**")
        lines.append(f"   {summary}")
        if tags:
            lines.append(f"   标签: {tags}")
        lines.append("")
    return "\n".join(lines).strip()


def _handle_general_chat(query: str) -> str:
    """Handle general conversation by delegating to the LLM.

    Args:
        query: User chat message.

    Returns:
        LLM-generated response.
    """
    try:
        return quick_chat(prompt=query)
    except Exception as exc:
        LOGGER.error("LLM chat failed: %s", exc)
        return f"抱歉，LLM 调用失败: {exc}"


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

# Dispatch table mapping intent → handler
_HANDLERS: dict[str, Any] = {
    "github_search": _handle_github_search,
    "knowledge_query": _handle_knowledge_query,
    "general_chat": _handle_general_chat,
}


def route(query: str) -> str:
    """Route a user query to the appropriate handler.

    Two-layer intent classification determines the handler:
      1. Keyword fast-match (zero-cost)
      2. LLM classification (fallback)

    Args:
        query: Raw user input string.

    Returns:
        Handler response as a string.
    """
    intent = classify_intent(query)
    handler = _HANDLERS.get(intent)
    if handler is None:
        LOGGER.error("No handler for intent=%r", intent)
        return "内部错误：未找到对应的处理器。"
    LOGGER.info("Routing query=%r → intent=%s", query[:80], intent)
    return handler(query)


# ---------------------------------------------------------------------------
# Tests (executed via ``python -m patterns.router [query]``)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ------------------------------------------------------------------
    # Single-query mode: ``python -m patterns.router "some query"``
    # ------------------------------------------------------------------
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"Query: {query}")
        try:
            result = route(query)
            print(f"\n{result}")
        except Exception as exc:
            LOGGER.error("Route failed: %s", exc)
            print(f"路由失败: {exc}")
        sys.exit(0)

    # ------------------------------------------------------------------
    # Test mode: classify a set of queries
    # ------------------------------------------------------------------
    # Check whether LLM is available for Layer 2 classification.
    provider_env = os.getenv("LLM_PROVIDER", "deepseek")
    key_vars = {
        "deepseek": "DEEPSEEK_API_KEY",
        "qwen": "QWEN_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    key_env = key_vars.get(provider_env, "DEEPSEEK_API_KEY")
    llm_available = bool(os.getenv(key_env))
    if not llm_available:
        print(f"⚠  LLM unavailable: {key_env} not set. "
              f"Keyword-only tests below; Layer 2 queries will be skipped.\n")

    test_queries: list[tuple[str, str, str | None]] = [
        ("帮我找一些关于 LLM agent 的开源项目", "github_search", None),
        ("知识库里最近有哪些 AI 相关的文章", "knowledge_query", None),
        ("今天天气怎么样", "general_chat", "keyword_miss"),
        ("GitHub trending AI projects", "github_search", None),
        ("什么是 transformer 架构", "general_chat", "keyword_miss"),
        ("有没有关于 LLM 的知识", "knowledge_query", None),
    ]

    print("=" * 60)
    print("Router 测试")
    print("=" * 60)

    for q, expected, layer1_result in test_queries:
        print(f"\n--- 查询: {q}")
        print(f"    期望意图: {expected}")

        if layer1_result == "keyword_miss" and not llm_available:
            print("    ⏭  跳过 (keyword 未命中, LLM 不可用)")
            continue

        intent = classify_intent(q)
        print(f"    实际意图: {intent}")

        if intent == expected:
            print("    ✓ 意图匹配")
        else:
            print(f"    ⚠ 意图不匹配 (期望 {expected}, 实际 {intent})")

        # Only route knowledge_query (no network / API key needed).
        if intent == "knowledge_query":
            try:
                result = route(q)
                preview = result[:200] + "..." if len(result) > 200 else result
                print(f"    路由结果: {preview}")
            except Exception as exc:
                print(f"    ✗ 路由失败: {exc}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    sys.exit(0)
