"""Knowledge base automation pipeline with command-line interface.

This module collects AI-related sources, analyzes them with the configured LLM,
normalizes them to the repository article schema, and saves each article as a
standalone JSON file. Network collection uses ``httpx.AsyncClient`` and is
wrapped with ``asyncio.run`` for synchronous CLI execution.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from model_client import chat_with_retry, get_provider


LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
DEFAULT_GITHUB_QUERY = "AI LLM Agent"
DEFAULT_STATUS = "draft"
DEFAULT_RSS_FEEDS = (
    {
        "name": "Hacker News",
        "url": "https://hnrss.org/frontpage",
    },
    {
        "name": "ArXiv AI",
        "url": "http://export.arxiv.org/rss/cs.AI",
    },
    {
        "name": "Papers With Code",
        "url": "https://paperswithcode.com/feed/latest",
    },
)

CHINA_TZ = timezone(timedelta(hours=8))
HTTP_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_SECONDS = 1.0
MAX_SUMMARY_CHARS = 200
MAX_TAGS = 5
MAX_KEY_INSIGHTS = 3

SOURCE_GITHUB = "github"
SOURCE_RSS = "rss"
VALID_SOURCES = {SOURCE_GITHUB, SOURCE_RSS}

REQUIRED_ARTICLE_FIELDS = (
    "id",
    "title",
    "source_url",
    "source_type",
    "summary",
    "tags",
    "status",
    "created_at",
    "updated_at",
)

XML_TEXT_RE = re.compile(r"<[^>]+>")
CDATA_RE = re.compile(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", re.DOTALL)
ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argparse namespace with sources, limit, dry_run, and verbose.
    """
    parser = argparse.ArgumentParser(
        description="Run the AI knowledge base automation pipeline."
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="Comma-separated sources to collect: github,rss.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum items per source collector. Defaults to 20.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip file writes and LLM calls while exercising the pipeline.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    """Configure logging for CLI execution.

    Args:
        verbose: Whether to enable debug-level output.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(message)s")
    dependency_level = logging.DEBUG if verbose else logging.WARNING
    logging.getLogger("httpx").setLevel(dependency_level)
    logging.getLogger("httpcore").setLevel(dependency_level)


def parse_sources(value: str) -> list[str]:
    """Parse and validate a comma-separated source list.

    Args:
        value: Comma-separated source names.

    Returns:
        Valid source names in the requested order without duplicates.

    Raises:
        ValueError: If an unknown source is requested.
    """
    sources: list[str] = []
    for source in value.split(","):
        normalized = source.strip().lower()
        if not normalized:
            continue
        if normalized not in VALID_SOURCES:
            expected = ",".join(sorted(VALID_SOURCES))
            raise ValueError(f"Unknown source '{normalized}'. Expected: {expected}")
        if normalized not in sources:
            sources.append(normalized)
    if not sources:
        raise ValueError("At least one source must be provided.")
    return sources


def current_time() -> datetime:
    """Return the current time in the required +08:00 timezone.

    Returns:
        Timezone-aware datetime in UTC+08:00.
    """
    return datetime.now(CHINA_TZ)


def iso_timestamp(moment: datetime | None = None) -> str:
    """Format a timestamp as ISO 8601 with +08:00 offset.

    Args:
        moment: Optional timestamp. Current +08:00 time is used when omitted.

    Returns:
        ISO 8601 timestamp string with seconds precision.
    """
    timestamp = moment or current_time()
    return timestamp.isoformat(timespec="seconds")


async def collect_github_async(limit: int) -> list[dict[str, Any]]:
    """Collect repositories from the GitHub Search API.

    Args:
        limit: Number of repositories requested from GitHub.

    Returns:
        Normalized raw GitHub items.
    """
    params = {
        "q": DEFAULT_GITHUB_QUERY,
        "sort": "stars",
        "order": "desc",
        "per_page": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            try:
                response = await client.get(GITHUB_SEARCH_URL, params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                LOGGER.warning("GitHub collection failed: %s", exc)
                return []
            finally:
                await asyncio.sleep(RATE_LIMIT_SECONDS)

        payload = response.json()
    except json.JSONDecodeError as exc:
        LOGGER.warning("GitHub response was not valid JSON: %s", exc)
        return []

    items = payload.get("items", [])
    if not isinstance(items, list):
        LOGGER.warning("GitHub response did not include an items array.")
        return []

    collected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = as_text(item.get("full_name"))
        author = full_name.split("/", maxsplit=1)[0] if full_name else ""
        source_url = as_text(item.get("html_url"))
        collected.append(
            {
                "source": SOURCE_GITHUB,
                "source_type": SOURCE_GITHUB,
                "name": as_text(item.get("name")),
                "title": as_text(item.get("name")),
                "source_url": source_url,
                "url": source_url,
                "description": as_text(item.get("description")),
                "stars": as_int(item.get("stargazers_count")),
                "language": as_text(item.get("language")),
                "topics": as_string_list(item.get("topics"), limit=MAX_TAGS),
                "full_name": full_name,
                "author": author,
            }
        )
    LOGGER.info("Collected %s GitHub items.", len(collected))
    return collected


async def collect_rss_async(limit: int) -> list[dict[str, Any]]:
    """Collect RSS items from the default feed list.

    Args:
        limit: Maximum RSS items across all feeds.

    Returns:
        Normalized raw RSS items.
    """
    collected: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        for feed in DEFAULT_RSS_FEEDS:
            if len(collected) >= limit:
                break
            feed_name = feed["name"]
            feed_url = feed["url"]
            try:
                response = await client.get(feed_url, follow_redirects=True)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                LOGGER.warning("RSS collection failed for %s: %s", feed_name, exc)
                continue

            remaining = limit - len(collected)
            parsed_items = parse_rss_items(response.text, feed_name, remaining)
            collected.extend(parsed_items)

    LOGGER.info("Collected %s RSS items.", len(collected))
    return collected


def collect_sources(sources: list[str], limit: int) -> list[dict[str, Any]]:
    """Collect raw items from selected sources.

    Args:
        sources: Source names selected by the CLI.
        limit: Maximum items per source collector.

    Returns:
        Combined raw item list.
    """
    collected: list[dict[str, Any]] = []
    if SOURCE_GITHUB in sources:
        collected.extend(asyncio.run(collect_github_async(limit)))
    if SOURCE_RSS in sources:
        collected.extend(asyncio.run(collect_rss_async(limit)))
    return collected


def parse_rss_items(
    xml_text: str,
    feed_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Parse RSS item blocks using regular expressions.

    Args:
        xml_text: RSS XML text.
        feed_name: Human-readable feed name.
        limit: Maximum parsed items to return.

    Returns:
        Normalized RSS items.
    """
    parsed: list[dict[str, Any]] = []
    for match in ITEM_RE.finditer(xml_text):
        if len(parsed) >= limit:
            break
        block = match.group(1)
        title = extract_xml_tag(block, "title")
        link = extract_xml_tag(block, "link")
        description = extract_xml_tag(block, "description")
        if not title and not link:
            continue
        parsed.append(
            {
                "source": SOURCE_RSS,
                "source_type": SOURCE_RSS,
                "title": title,
                "source_url": link,
                "url": link,
                "description": description,
                "feed_name": feed_name,
            }
        )
    return parsed


def extract_xml_tag(block: str, tag_name: str) -> str:
    """Extract a tag value from an XML block.

    Args:
        block: XML fragment to search.
        tag_name: Name of the tag to extract.

    Returns:
        Cleaned text value, or an empty string when absent.
    """
    pattern = rf"<{tag_name}\b[^>]*>(.*?)</{tag_name}>"
    match = re.search(pattern, block, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return clean_xml_text(match.group(1))


def clean_xml_text(value: str) -> str:
    """Clean XML text, CDATA wrappers, tags, and HTML entities.

    Args:
        value: Raw XML tag content.

    Returns:
        Human-readable text.
    """
    cdata_match = CDATA_RE.match(value)
    if cdata_match:
        value = cdata_match.group(1)
    without_tags = XML_TEXT_RE.sub(" ", value)
    normalized = " ".join(without_tags.split())
    return html.unescape(normalized).strip()


def save_raw_items(
    items: list[dict[str, Any]],
    sources: list[str],
    dry_run: bool,
) -> Path | None:
    """Save collected raw items with a metadata header.

    Args:
        items: Raw collected items.
        sources: Sources included in the collection run.
        dry_run: Whether file writing should be skipped.

    Returns:
        Path to the raw JSON file, or None when skipped or failed.
    """
    if dry_run:
        LOGGER.info("Dry run enabled; skipping raw data write.")
        return None

    collected_at = current_time()
    filename = f"pipeline-{collected_at.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "source": ",".join(sources),
        "collected_at": iso_timestamp(collected_at),
        "query": DEFAULT_GITHUB_QUERY,
        "items": items,
    }
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = RAW_DIR / filename
        raw_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("Failed to save raw pipeline data: %s", exc)
        return None

    LOGGER.info("Saved raw pipeline data to %s.", raw_path)
    return raw_path


def analyze_items(
    items: list[dict[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Analyze raw items with the configured LLM or dry-run mock data.

    Args:
        items: Raw collected items.
        dry_run: Whether LLM calls should be skipped.

    Returns:
        Items with an ``analysis`` dictionary attached.
    """
    if dry_run:
        return [{**item, "analysis": mock_analysis(item)} for item in items]

    try:
        provider = get_provider()
    except (RuntimeError, ValueError, OSError) as exc:
        LOGGER.warning("LLM provider initialization failed: %s", exc)
        return [{**item, "analysis": default_analysis(item)} for item in items]

    analyzed: list[dict[str, Any]] = []
    for item in items:
        analyzed.append(analyze_item(item, provider))
        time.sleep(RATE_LIMIT_SECONDS)
    return analyzed


def analyze_item(item: dict[str, Any], provider: Any) -> dict[str, Any]:
    """Analyze one item with ``chat_with_retry``.

    Args:
        item: Raw collected item.
        provider: Provider object returned by ``get_provider``.

    Returns:
        Item copy with normalized analysis data.
    """
    messages = build_analysis_messages(item)
    source_url = as_text(item.get("source_url"))
    try:
        response = chat_with_retry(
            messages,
            provider=provider,
            temperature=0.2,
            max_tokens=700,
        )
        analysis = parse_llm_analysis(response.content, item)
    except (RuntimeError, ValueError, TypeError, TimeoutError, OSError) as exc:
        LOGGER.warning("LLM analysis failed for %s: %s", source_url, exc)
        analysis = default_analysis(item)
    return {**item, "analysis": analysis}


def build_analysis_messages(item: dict[str, Any]) -> list[dict[str, str]]:
    """Build Chinese-language analysis prompts for the LLM.

    Args:
        item: Raw collected item.

    Returns:
        Chat messages for ``chat_with_retry``.
    """
    source_payload = {
        "title": item.get("title") or item.get("name"),
        "source_url": item.get("source_url"),
        "source_type": item.get("source_type"),
        "description": item.get("description"),
        "language": item.get("language"),
        "topics": item.get("topics"),
        "stars": item.get("stars"),
        "feed_name": item.get("feed_name"),
    }
    item_json = json.dumps(source_payload, ensure_ascii=False, indent=2)
    system_prompt = (
        "你是AI技术知识库分析助手。请严格返回合法JSON对象，不要添加"
        "Markdown代码块、解释文字或额外字段。"
    )
    user_prompt = (
        "请分析以下技术条目与AI、LLM、Agent领域的相关性，并返回JSON：\n"
        "{\n"
        '  "summary": "200字以内中文摘要",\n'
        '  "relevance_score": 0.0,\n'
        '  "tags": ["最多5个标签"],\n'
        '  "category": "一个分类",\n'
        '  "key_insights": ["最多3个关键洞察"]\n'
        "}\n\n"
        f"条目内容：\n{item_json}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def parse_llm_analysis(
    content: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    """Parse and normalize LLM JSON analysis.

    Args:
        content: Raw LLM response content.
        item: Source item used for fallback defaults.

    Returns:
        Normalized analysis dictionary.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        json_text = extract_json_object(content)
        if not json_text:
            LOGGER.warning("LLM response did not contain a JSON object.")
            return default_analysis(item)
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            LOGGER.warning("Failed to parse LLM JSON response: %s", exc)
            return default_analysis(item)

    if not isinstance(parsed, dict):
        LOGGER.warning("LLM response JSON was not an object.")
        return default_analysis(item)
    return normalize_analysis(parsed, item)


def extract_json_object(content: str) -> str:
    """Extract the first JSON object-looking substring from text.

    Args:
        content: Raw text that may include a JSON object.

    Returns:
        JSON object substring or an empty string.
    """
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return content[start : end + 1]


def normalize_analysis(
    parsed: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    """Normalize LLM analysis fields and enforce limits.

    Args:
        parsed: Parsed LLM response object.
        item: Source item used for fallback defaults.

    Returns:
        Normalized analysis dictionary.
    """
    fallback = default_analysis(item)
    summary = as_text(parsed.get("summary")) or fallback["summary"]
    tags = as_string_list(parsed.get("tags"), limit=MAX_TAGS)
    insights = as_string_list(
        parsed.get("key_insights"),
        limit=MAX_KEY_INSIGHTS,
    )
    return {
        "summary": summary[:MAX_SUMMARY_CHARS],
        "relevance_score": clamp_score(parsed.get("relevance_score")),
        "tags": tags or fallback["tags"],
        "category": as_text(parsed.get("category")) or fallback["category"],
        "key_insights": insights or fallback["key_insights"],
    }


def mock_analysis(item: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic dummy analysis for dry-run mode.

    Args:
        item: Raw collected item.

    Returns:
        Mock analysis with the same shape as LLM output.
    """
    title = item.get("title") or item.get("name") or "未知条目"
    return {
        "summary": f"模拟摘要：{as_text(title)}，用于dry-run流程验证。"[
            :MAX_SUMMARY_CHARS
        ],
        "relevance_score": 0.5,
        "tags": default_tags(item),
        "category": "模拟分类",
        "key_insights": ["dry-run模式未调用LLM"],
    }


def default_analysis(item: dict[str, Any]) -> dict[str, Any]:
    """Build fallback analysis when LLM parsing or calls fail.

    Args:
        item: Raw collected item.

    Returns:
        Conservative default analysis.
    """
    title = item.get("title") or item.get("name") or "未知条目"
    description = as_text(item.get("description"))
    base_summary = description or f"{as_text(title)} 的AI相关性需要人工复核。"
    return {
        "summary": base_summary[:MAX_SUMMARY_CHARS],
        "relevance_score": 0.0,
        "tags": default_tags(item),
        "category": "未分类",
        "key_insights": ["LLM分析不可用，已使用默认结果。"],
    }


def default_tags(item: dict[str, Any]) -> list[str]:
    """Derive fallback tags from source metadata.

    Args:
        item: Raw collected item.

    Returns:
        Non-empty tag list capped to the schema limit.
    """
    topics = as_string_list(item.get("topics"), limit=MAX_TAGS)
    if topics:
        return topics[:MAX_TAGS]
    language = as_text(item.get("language"))
    if language:
        return [language]
    return ["AI"]


def organize_articles(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate analyzed items and convert them to article schema.

    Args:
        items: Analyzed items.

    Returns:
        Valid articles in AGENTS.md-compatible schema.
    """
    deduplicated = deduplicate_items(items)
    counters = {SOURCE_GITHUB: 0, SOURCE_RSS: 0}
    created_at = current_time()
    articles: list[dict[str, Any]] = []
    for item in deduplicated:
        article = build_article(item, counters, created_at)
        if validate_article(article):
            articles.append(article)
    LOGGER.info("Organized %s valid articles.", len(articles))
    return articles


def deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate items by source URL, keeping richer metadata.

    Args:
        items: Analyzed items that may include duplicate URLs.

    Returns:
        Deduplicated items.
    """
    best_by_url: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        source_url = as_text(item.get("source_url"))
        key = source_url or f"missing-url-{index}"
        existing = best_by_url.get(key)
        if existing is None:
            best_by_url[key] = item
            continue
        if metadata_score(item) > metadata_score(existing):
            best_by_url[key] = item
    return list(best_by_url.values())


def metadata_score(item: dict[str, Any]) -> int:
    """Score item richness for duplicate resolution.

    Args:
        item: Raw or analyzed item.

    Returns:
        Score based on stars and populated metadata fields.
    """
    score = as_int(item.get("stars"))
    metadata_keys = (
        "description",
        "language",
        "author",
        "feed_name",
        "full_name",
        "topics",
        "analysis",
    )
    for key in metadata_keys:
        value = item.get(key)
        if value:
            score += 1
    return score


def build_article(
    item: dict[str, Any],
    counters: dict[str, int],
    created_at: datetime,
) -> dict[str, Any]:
    """Convert an analyzed item to the article JSON schema.

    Args:
        item: Analyzed source item.
        counters: Per-source daily counters for article IDs.
        created_at: Timestamp to use for created_at and updated_at.

    Returns:
        Article dictionary following the required schema.
    """
    source_type = normalize_source_type(item.get("source_type") or item.get("source"))
    counters[source_type] = counters.get(source_type, 0) + 1
    article_id = build_article_id(source_type, created_at, counters[source_type])
    analysis = normalize_analysis(item.get("analysis", {}), item)
    timestamp = iso_timestamp(created_at)
    return {
        "id": article_id,
        "title": article_title(item),
        "source_url": as_text(item.get("source_url")),
        "source_type": source_type,
        "summary": analysis["summary"],
        "tags": analysis["tags"],
        "status": DEFAULT_STATUS,
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": article_metadata(item, source_type),
        "ai_analysis": {
            "relevance_score": analysis["relevance_score"],
            "key_insights": analysis["key_insights"],
            "category": analysis["category"],
        },
    }


def build_article_id(source_type: str, created_at: datetime, counter: int) -> str:
    """Build a source-date-counter article ID.

    Args:
        source_type: Source name such as github or rss.
        created_at: Timestamp used for the date segment.
        counter: Per-source daily counter.

    Returns:
        Article ID in ``{source}-{YYYYMMDD}-{NNN}`` format.
    """
    return f"{source_type}-{created_at.strftime('%Y%m%d')}-{counter:03d}"


def normalize_source_type(value: Any) -> str:
    """Normalize source type to one of the supported article values.

    Args:
        value: Raw source value.

    Returns:
        ``github`` or ``rss``. Unknown values default to ``rss``.
    """
    source_type = as_text(value).lower()
    if source_type == SOURCE_GITHUB:
        return SOURCE_GITHUB
    if source_type == SOURCE_RSS:
        return SOURCE_RSS
    LOGGER.warning("Unknown source_type '%s'; defaulting to rss.", source_type)
    return SOURCE_RSS


def article_title(item: dict[str, Any]) -> str:
    """Resolve an article title from source fields.

    Args:
        item: Source item.

    Returns:
        Article title string.
    """
    return as_text(item.get("title") or item.get("name"))


def article_metadata(item: dict[str, Any], source_type: str) -> dict[str, Any]:
    """Build source-specific article metadata.

    Args:
        item: Source item.
        source_type: Normalized source type.

    Returns:
        Metadata dictionary required for the source.
    """
    if source_type == SOURCE_GITHUB:
        return {
            "stars": as_int(item.get("stars")),
            "language": as_text(item.get("language")),
            "author": as_text(item.get("author")),
        }
    return {"feed_name": as_text(item.get("feed_name"))}


def validate_article(article: dict[str, Any]) -> bool:
    """Validate required article fields and warn about optional fields.

    Args:
        article: Article dictionary to validate.

    Returns:
        True when all required fields are present, otherwise False.
    """
    missing = [field for field in REQUIRED_ARTICLE_FIELDS if not article.get(field)]
    if missing:
        LOGGER.warning(
            "Article %s missing required fields: %s",
            article.get("id", "<unknown>"),
            ", ".join(missing),
        )
        return False

    source_type = article.get("source_type")
    if source_type not in VALID_SOURCES:
        LOGGER.warning("Article %s has invalid source_type.", article["id"])
        return False

    if not article.get("metadata"):
        LOGGER.warning("Article %s missing optional metadata.", article["id"])
    if not article.get("ai_analysis"):
        LOGGER.warning("Article %s missing optional ai_analysis.", article["id"])
    return True


def save_articles(articles: list[dict[str, Any]], dry_run: bool) -> int:
    """Save each article as a standalone JSON file.

    Args:
        articles: Valid article dictionaries.
        dry_run: Whether file writing should be skipped.

    Returns:
        Number of articles written.
    """
    if dry_run:
        LOGGER.info("Dry run enabled; skipping article writes.")
        return 0

    saved = 0
    try:
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        LOGGER.warning("Failed to create article directory: %s", exc)
        return 0

    for article in articles:
        path = ARTICLES_DIR / f"{article['id']}.json"
        try:
            path.write_text(
                json.dumps(article, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("Failed to save article %s: %s", article["id"], exc)
            continue
        saved += 1
    return saved


def as_text(value: Any) -> str:
    """Convert a value to stripped text.

    Args:
        value: Value to convert.

    Returns:
        Stripped string, or empty string for None.
    """
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any) -> int:
    """Convert a value to int with a safe fallback.

    Args:
        value: Value to convert.

    Returns:
        Integer value, or zero when conversion fails.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_string_list(value: Any, limit: int) -> list[str]:
    """Normalize a value to a list of non-empty strings.

    Args:
        value: Source value that may already be a list.
        limit: Maximum number of strings to return.

    Returns:
        List of stripped strings.
    """
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    elif value:
        raw_items = [value]
    else:
        raw_items = []
    strings = [as_text(item) for item in raw_items]
    return [item for item in strings if item][:limit]


def clamp_score(value: Any) -> float:
    """Convert a relevance score to a 0.0-1.0 float.

    Args:
        value: Raw relevance score.

    Returns:
        Clamped score.
    """
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def run_pipeline(
    sources: list[str],
    limit: int,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Run the four-step knowledge base pipeline.

    Args:
        sources: Source names selected for collection.
        limit: Maximum items per source collector.
        dry_run: Whether to skip persistent writes and LLM calls.

    Returns:
        Tuple of collected, analyzed, and saved counts.
    """
    collected = collect_sources(sources, limit)
    save_raw_items(collected, sources, dry_run)

    analyzed = analyze_items(collected, dry_run)
    articles = organize_articles(analyzed)
    saved = save_articles(articles, dry_run)

    return len(collected), len(analyzed), saved


def main() -> int:
    """Run the CLI entry point.

    Returns:
        Process exit code.
    """
    args = parse_args()
    configure_logging(args.verbose)
    try:
        sources = parse_sources(args.sources)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 2

    if args.limit < 1:
        LOGGER.error("--limit must be a positive integer.")
        return 2

    collected, analyzed, saved = run_pipeline(
        sources=sources,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    LOGGER.info(
        "Collected %s items, Analyzed %s, Saved %s articles.",
        collected,
        analyzed,
        saved,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
