#!/usr/bin/env python3
"""Validate knowledge entry JSON files.

Usage:
    python hooks/validate_json.py <json_file> [json_file2 ...]

Supports glob patterns (e.g., *.json) and multiple file arguments.
Exit codes:
    0 - all files passed validation
    1 - one or more files failed validation
"""

import glob
import json
import re
import sys
from pathlib import Path
from typing import Any


# Required fields with expected types
REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}

# ID pattern: {source}-{YYYYMMDD}-{NNN}
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+-\d{8}-\d{3}$")

# URL pattern: must start with http:// or https://
URL_PATTERN = re.compile(r"^https?://")


def validate_id(value: str, errors: list[str]) -> None:
    """Validate ID format: {source}-{YYYYMMDD}-{NNN}."""
    if not isinstance(value, str):
        return  # type error handled elsewhere
    if not ID_PATTERN.match(value):
        errors.append(
            f"Invalid id format '{value}'. Expected: {{source}}-YYYYMMDD-NNN "
            f"(e.g., github-20260317-001)"
        )


def validate_status(value: str, errors: list[str]) -> None:
    """Validate status is one of the allowed values."""
    if not isinstance(value, str):
        return
    if value not in VALID_STATUSES:
        errors.append(
            f"Invalid status '{value}'. Must be one of: "
            f"{', '.join(sorted(VALID_STATUSES))}"
        )


def validate_url(value: str, errors: list[str]) -> None:
    """Validate URL starts with http:// or https://."""
    if not isinstance(value, str):
        return
    if not URL_PATTERN.match(value):
        errors.append(
            f"Invalid source_url '{value}'. Must start with http:// or https://"
        )


def validate_summary(value: str, errors: list[str]) -> None:
    """Validate summary has at least 20 characters."""
    if not isinstance(value, str):
        return
    if len(value) < 20:
        errors.append(
            f"Summary too short ({len(value)} chars). Minimum 20 characters required."
        )


def validate_tags(value: list[Any], errors: list[str]) -> None:
    """Validate tags is a non-empty list."""
    if not isinstance(value, list):
        return
    if len(value) < 1:
        errors.append("Tags must contain at least 1 item.")


def validate_score(value: Any, errors: list[str]) -> None:
    """Validate optional score field is in range 1-10."""
    if value is None:
        return
    if not isinstance(value, (int, float)):
        errors.append(f"Score must be a number, got {type(value).__name__}.")
        return
    if not (1 <= value <= 10):
        errors.append(f"Score {value} out of range. Must be between 1 and 10.")


def validate_audience(value: Any, errors: list[str]) -> None:
    """Validate optional audience field is one of allowed values."""
    if value is None:
        return
    if not isinstance(value, str):
        errors.append(
            f"Audience must be a string, got {type(value).__name__}."
        )
        return
    if value not in VALID_AUDIENCES:
        errors.append(
            f"Invalid audience '{value}'. Must be one of: "
            f"{', '.join(sorted(VALID_AUDIENCES))}"
        )


def validate_entry(entry: dict[str, Any], index: int | None = None) -> list[str]:
    """Validate a single knowledge entry dictionary.

    Args:
        entry: The dictionary to validate.
        index: Optional index for array entries (used in error messages).

    Returns:
        List of error messages. Empty list if valid.
    """
    errors: list[str] = []
    prefix = f"Entry[{index}]: " if index is not None else ""

    # Check required fields and types
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in entry:
            errors.append(f"{prefix}Missing required field: '{field}'")
            continue
        value = entry[field]
        if not isinstance(value, expected_type):
            errors.append(
                f"{prefix}Field '{field}' must be {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )

    # Validate specific fields
    if "id" in entry:
        id_errors: list[str] = []
        validate_id(entry["id"], id_errors)
        errors.extend(f"{prefix}{e}" for e in id_errors)

    if "status" in entry:
        status_errors: list[str] = []
        validate_status(entry["status"], status_errors)
        errors.extend(f"{prefix}{e}" for e in status_errors)

    if "source_url" in entry:
        url_errors: list[str] = []
        validate_url(entry["source_url"], url_errors)
        errors.extend(f"{prefix}{e}" for e in url_errors)

    if "summary" in entry:
        summary_errors: list[str] = []
        validate_summary(entry["summary"], summary_errors)
        errors.extend(f"{prefix}{e}" for e in summary_errors)

    if "tags" in entry:
        tag_errors: list[str] = []
        validate_tags(entry["tags"], tag_errors)
        errors.extend(f"{prefix}{e}" for e in tag_errors)

    # Optional fields
    score = entry.get("score")
    if score is not None or "score" in entry:
        score_errors: list[str] = []
        validate_score(score, score_errors)
        errors.extend(f"{prefix}{e}" for e in score_errors)

    audience = entry.get("audience")
    if audience is not None or "audience" in entry:
        audience_errors: list[str] = []
        validate_audience(audience, audience_errors)
        errors.extend(f"{prefix}{e}" for e in audience_errors)

    return errors


def validate_file(file_path: Path) -> tuple[list[str], int]:
    """Validate a single JSON file.

    Args:
        file_path: Path to the JSON file.

    Returns:
        Tuple of (error_messages, entry_count).
    """
    errors: list[str] = []
    entry_count = 0

    if not file_path.exists():
        return [f"File not found: {file_path}"], 0

    if not file_path.is_file():
        return [f"Not a file: {file_path}"], 0

    try:
        with file_path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON: {exc}"], 0
    except OSError as exc:
        return [f"Cannot read file: {exc}"], 0

    if isinstance(data, dict):
        entry_count = 1
        entry_errors = validate_entry(data)
        if entry_errors:
            errors.extend(entry_errors)
    elif isinstance(data, list):
        entry_count = len(data)
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"Entry[{idx}]: Expected dict, got {type(item).__name__}")
                continue
            entry_errors = validate_entry(item, idx)
            errors.extend(entry_errors)
    else:
        errors.append(
            f"Expected JSON object or array, got {type(data).__name__}"
        )

    return errors, entry_count


def expand_paths(args: list[str]) -> list[Path]:
    """Expand command line arguments to file paths.

    Supports glob patterns and regular file paths.
    """
    paths: list[Path] = []
    for arg in args:
        # Try glob expansion first
        matches = glob.glob(arg)
        if matches:
            paths.extend(Path(m) for m in matches)
        else:
            # No glob matches, treat as literal path
            paths.append(Path(arg))
    return paths


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    if len(sys.argv) < 2:
        print(
            "Usage: python hooks/validate_json.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        print("Supports glob patterns (e.g., *.json)", file=sys.stderr)
        return 1

    file_paths = expand_paths(sys.argv[1:])

    total_files = 0
    total_entries = 0
    passed_files = 0
    failed_files = 0
    all_errors: list[str] = []

    for file_path in file_paths:
        total_files += 1
        print(f"\n📄 {file_path}")

        errors, entry_count = validate_file(file_path)
        total_entries += entry_count

        if errors:
            failed_files += 1
            print(f"   ❌ FAILED ({entry_count} entries)")
            for error in errors:
                print(f"      - {error}")
            all_errors.extend(f"{file_path}: {e}" for e in errors)
        else:
            passed_files += 1
            print(f"   ✅ PASSED ({entry_count} entries)")

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Total files:   {total_files}")
    print(f"Total entries: {total_entries}")
    print(f"Passed:        {passed_files}")
    print(f"Failed:        {failed_files}")

    if all_errors:
        print(f"\nTotal errors:  {len(all_errors)}")
        return 1

    print("\n✅ All files passed validation!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
