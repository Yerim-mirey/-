"""Batch RouteMatrix destinations without exceeding Baidu's route limit."""

from collections.abc import Iterator, Sequence
from typing import TypeVar


WALKING_MATRIX_MAX_ROUTES = 50
T = TypeVar("T")


def iter_batches(items: Sequence[T], size: int = WALKING_MATRIX_MAX_ROUTES) -> Iterator[list[T]]:
    if size <= 0:
        raise ValueError("batch size 必须大于 0。")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])
