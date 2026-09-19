"""Baidu search keywords for the public POI facility types."""

from typing import Final


CATEGORY_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "market": ("菜市场", "农贸市场", "生鲜市场"),
    "pharmacy": ("药店", "药房"),
    "primary_school": ("小学",),
}
