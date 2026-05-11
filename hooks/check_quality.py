#!/usr/bin/env python3
"""Quality scoring for knowledge entry JSON files."""

import glob
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STANDARD_TAGS = {
    "AI", "LLM", "Agent", "RAG", "NLP", "Multimodal",
    "Machine Learning", "Deep Learning", "Transformers",
    "Diffusion", "Inference", "Local-LLM",
    "Framework", "Tool", "DevTool", "Platform", "Open-Source",
    "Python", "TypeScript", "JavaScript", "Go", "Rust", "C++", "Java", "Shell",
    "HTML", "Scala", "Kotlin", "Swift",
    "Automation", "Workflow", "ChatBot", "Image Generation",
    "AI-Art", "Privacy", "Cross-Platform", "Low-Code",
    "Methodology", "DevOps", "MCP", "Prompt Engineering",
    "Community", "Productivity", "Assistant",
    "本地化", "隐私", "边缘计算", "私有化",
}

BUZZWORDS_CN = {
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
}

BUZZWORDS_EN = {
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "disruptive", "synergy", "leverage",
    "holistic", "seamless", "scalable", "robust",
    "best-in-class", "world-class", "industry-leading",
}

TECH_KEYWORDS = {
    "API", "GPU", "CPU", "分布式", "并行", "量化", "微调",
    "推理", "训练", "部署", "模型", "架构", "协议",
    "benchmark", "latency", "throughput", "token",
    "Docker", "Kubernetes", "REST", "WebSocket",
    "向量数据库", "embedding", "LoRA", "RAG",
    "agent", "workflow", "pipeline", "orchestration",
}

GRADE_A_THRESHOLD = 80
GRADE_B_THRESHOLD = 60

@dataclass
class DimensionScore:
    name: str
    score: float
    max_score: float
    details: str = ""

    @property
    def percentage(self) -> float:
        if self.max_score == 0:
            return 0.0
        return (self.score / self.max_score) * 100

@dataclass
class QualityReport:
    file_path: Path
    entry_id: str
    title: str
    dimensions: list = field(default_factory=list)
    total_score: float = 0.0
    max_total: float = 100.0
    grade: str = "C"

    def compute_total(self) -> None:
        self.total_score = sum(d.score for d in self.dimensions)
        self.max_total = sum(d.max_score for d in self.dimensions)
        if self.total_score >= GRADE_A_THRESHOLD:
            self.grade = "A"
        elif self.total_score >= GRADE_B_THRESHOLD:
            self.grade = "B"
        else:
            self.grade = "C"

def _count_chars(text: str) -> int:
    return len(text)

def _has_buzzwords(text: str) -> list:
    found = []
    text_lower = text.lower()
    for word in BUZZWORDS_CN:
        if word in text:
            found.append(word)
    for word in BUZZWORDS_EN:
        if word in text_lower:
            found.append(word)
    return found

def _has_tech_keywords(text: str) -> int:
    count = 0
    text_lower = text.lower()
    for kw in TECH_KEYWORDS:
        if kw.lower() in text_lower:
            count += 1
    return count

def _bar(score: float, max_score: float, width: int = 20) -> str:
    if max_score == 0:
        filled = 0
    else:
        filled = int(round((score / max_score) * width))
    filled = max(0, min(filled, width))
    empty = width - filled
    return "█" * filled + "░" * empty

def score_summary(entry: dict) -> DimensionScore:
    summary = entry.get("summary", "")
    if not isinstance(summary, str):
        return DimensionScore("摘要质量", 0.0, 25.0, "summary not string")
    char_count = _count_chars(summary)
    base_score = 20.0 if char_count >= 50 else (10.0 if char_count >= 20 else 0.0)
    tech_bonus = min(_has_tech_keywords(summary) * 1.0, 5.0)
    total = min(base_score + tech_bonus, 25.0)
    return DimensionScore("摘要质量", total, 25.0, f"{char_count} chars, tech keywords +{tech_bonus:.0f}")

def score_technical_depth(entry: dict) -> DimensionScore:
    score_value = None
    ai_analysis = entry.get("ai_analysis")
    if isinstance(ai_analysis, dict):
        raw = ai_analysis.get("relevance_score")
        if isinstance(raw, (int, float)):
            score_value = float(raw)
    if score_value is None:
        metadata = entry.get("metadata")
        if isinstance(metadata, dict):
            stars = metadata.get("stars")
            if isinstance(stars, (int, float)) and stars > 0:
                score_value = min(10.0, max(1.0, stars / 20000))
    if score_value is None:
        return DimensionScore("技术深度", 0.0, 25.0, "no score data")
    mapped = ((score_value - 1) / 9) * 25
    mapped = max(0.0, min(25.0, mapped))
    return DimensionScore("技术深度", round(mapped, 1), 25.0, f"raw {score_value}/10 -> {mapped:.1f}/25")

def score_format_compliance(entry: dict) -> DimensionScore:
    checks = {
        "id": bool(entry.get("id")),
        "title": bool(entry.get("title")),
        "source_url": bool(entry.get("source_url")),
        "status": bool(entry.get("status")),
        "timestamp": bool(entry.get("created_at") or entry.get("updated_at")),
    }
    passed = sum(1 for v in checks.values() if v)
    score = passed * 4.0
    details = ", ".join(f"{k}: {'OK' if v else 'MISS'}" for k, v in checks.items())
    return DimensionScore("格式规范", score, 20.0, details)

def score_tag_precision(entry: dict) -> DimensionScore:
    tags = entry.get("tags", [])
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 0.0, 15.0, "tags not list")
    count = len(tags)
    if count == 0:
        base = 0.0
    elif 1 <= count <= 3:
        base = 15.0
    elif 4 <= count <= 5:
        base = 10.0
    else:
        base = 5.0
    penalty = 0
    non_standard = []
    for tag in tags:
        if isinstance(tag, str) and tag not in STANDARD_TAGS:
            penalty += 1
            non_standard.append(tag)
    score = max(0.0, base - penalty)
    details = f"{count} tags"
    if non_standard:
        details += f", non-standard: {', '.join(non_standard)} (-{penalty})"
    return DimensionScore("标签精度", score, 15.0, details)

def score_buzzword_free(entry: dict) -> DimensionScore:
    summary = entry.get("summary", "")
    title = entry.get("title", "")
    text = f"{title} {summary}"
    found = _has_buzzwords(text)
    count = len(found)
    if count == 0:
        score = 15.0
    elif count == 1:
        score = 8.0
    else:
        score = 0.0
    details = f"buzzwords {count}"
    if found:
        details += f": {', '.join(found)}"
    else:
        details += " (none)"
    return DimensionScore("空洞词检测", score, 15.0, details)

def score_entry(entry: dict, file_path: Path) -> QualityReport:
    entry_id = entry.get("id", "unknown")
    title = entry.get("title", "Untitled")
    report = QualityReport(file_path, str(entry_id), str(title))
    report.dimensions = [
        score_summary(entry),
        score_technical_depth(entry),
        score_format_compliance(entry),
        score_tag_precision(entry),
        score_buzzword_free(entry),
    ]
    report.compute_total()
    return report

def process_file(file_path: Path) -> tuple:
    reports = []
    errors = []
    if not file_path.exists():
        return [], [f"File not found: {file_path}"]
    if not file_path.is_file():
        return [], [f"Not a file: {file_path}"]
    try:
        with file_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return [], [f"Invalid JSON: {exc}"]
    except OSError as exc:
        return [], [f"Cannot read file: {exc}"]
    if isinstance(data, dict):
        reports.append(score_entry(data, file_path))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"Entry[{idx}]: Expected dict, got {type(item).__name__}")
                continue
            reports.append(score_entry(item, file_path))
    else:
        errors.append(f"Expected JSON object or array, got {type(data).__name__}")
    return reports, errors

def expand_paths(args: list) -> list:
    paths = []
    for arg in args:
        matches = glob.glob(arg)
        if matches:
            paths.extend(Path(m) for m in matches)
        else:
            paths.append(Path(arg))
    return paths

def print_report(report: QualityReport, index: int = None) -> None:
    prefix = f"[{index}] " if index is not None else ""
    print(f"\n{prefix}📄 {report.file_path}")
    print(f"    ID:    {report.entry_id}")
    print(f"    Title: {report.title}")
    grade_icon = {"A": "[A]", "B": "[B]", "C": "[C]"}.get(report.grade, "[?]")
    print(f"    Grade: {grade_icon} {report.grade}  ({report.total_score:.1f}/{report.max_total})")
    print(f"    {'-' * 46}")
    for dim in report.dimensions:
        bar = _bar(dim.score, dim.max_score, width=20)
        pct = dim.percentage
        print(f"    {dim.name:8s} {bar} {dim.score:5.1f}/{dim.max_score:.0f} ({pct:5.1f}%)")
        print(f"             +-- {dim.details}")

def print_summary(total_entries: int, grade_counts: dict, dimension_totals: dict) -> None:
    line = "=" * 54
    print(f"\n{line}")
    print("📊 OVERALL SUMMARY")
    print(line)
    print(f"Total entries: {total_entries}")
    print(f"  [A] Grade A: {grade_counts.get('A', 0)}")
    print(f"  [B] Grade B: {grade_counts.get('B', 0)}")
    print(f"  [C] Grade C: {grade_counts.get('C', 0)}")
    print(line)
    print("AVERAGE DIMENSION SCORES")
    print(line)
    for dim_name, (total, max_total) in dimension_totals.items():
        avg = total / total_entries if total_entries else 0
        max_avg = max_total / total_entries if total_entries else 0
        bar = _bar(avg, max_avg, width=20)
        pct = (avg / max_avg * 100) if max_avg else 0
        print(f"  {dim_name:8s} {bar} {avg:5.1f}/{max_avg:.0f} ({pct:5.1f}%)")
    print(line)
    has_c = grade_counts.get("C", 0) > 0
    if has_c:
        print("⚠️  Grade C entries found. Quality check FAILED.")
    else:
        print("✅ No Grade C entries. Quality check PASSED.")
    print(line)

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python hooks/check_quality.py <json_file> [json_file2 ...]", file=sys.stderr)
        print("Supports glob patterns (e.g., *.json)", file=sys.stderr)
        return 1
    file_paths = expand_paths(sys.argv[1:])
    all_reports = []
    all_errors = []
    grade_counts = {"A": 0, "B": 0, "C": 0}
    dimension_totals = {}
    total_entries = 0
    for file_path in file_paths:
        reports, errors = process_file(file_path)
        all_reports.extend(reports)
        all_errors.extend(errors)
        for report in reports:
            total_entries += 1
            grade_counts[report.grade] = grade_counts.get(report.grade, 0) + 1
            for dim in report.dimensions:
                if dim.name not in dimension_totals:
                    dimension_totals[dim.name] = [0.0, 0.0]
                dimension_totals[dim.name][0] += dim.score
                dimension_totals[dim.name][1] += dim.max_score
    for idx, report in enumerate(all_reports):
        print_report(report, idx)
    print_summary(total_entries, grade_counts, dimension_totals)
    if all_errors:
        print(f"\n⚠️  ERRORS ({len(all_errors)}):")
        for err in all_errors:
            print(f"  - {err}")
    return 1 if grade_counts.get("C", 0) > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
