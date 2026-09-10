from __future__ import annotations

from typing import Any

from rh_cli.errors import RhCliError


RATING_TAGS = frozenset(str(score) for score in range(1, 6))


def normalize_library_rating(value: Any) -> int:
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise RhCliError("INVALID_LIBRARY_RATING", "积木评分必须是 0 到 5。") from exc
    if score < 0 or score > 5:
        raise RhCliError("INVALID_LIBRARY_RATING", "积木评分必须是 0 到 5。")
    return score


def replace_rating_tag(tags: list[str], rating: int) -> list[str]:
    result = [str(tag).strip() for tag in tags if str(tag).strip() not in RATING_TAGS]
    if rating:
        result.append(str(rating))
    return result
