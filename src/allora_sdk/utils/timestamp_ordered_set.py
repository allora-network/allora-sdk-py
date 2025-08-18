from typing import Iterator, Tuple, Union, Optional
from collections import OrderedDict
import time


class TimestampOrderedSet:
    """
    An ordered set where ordering is based on insertion timestamp.

    Stores tuples of (timestamp, integer_value) and maintains insertion order
    while ensuring uniqueness based on the integer value.
    """

    def __init__(self):
        self._data: OrderedDict[int, float] = OrderedDict()

    def add(self, value: int, timestamp: Optional[float] = None) -> None:
        """
        Add a value with timestamp. If value already exists, update its timestamp
        and move it to the end (most recent position).

        Args:
            value: The integer value to add
            timestamp: Unix timestamp (float). If None, uses current time.
        """
        if timestamp is None:
            timestamp = time.time()

        # Remove if exists (to update position) then add at end
        if value in self._data:
            del self._data[value]
        self._data[value] = timestamp

    def discard(self, value: int) -> None:
        """Remove a value if it exists (no error if not found)."""
        self._data.pop(value, None)

    def remove(self, value: int) -> None:
        """Remove a value (raises KeyError if not found)."""
        del self._data[value]

    def __contains__(self, value: int) -> bool:
        """Check if a value is in the set."""
        return value in self._data

    def __len__(self) -> int:
        """Return the number of items in the set."""
        return len(self._data)

    def __iter__(self) -> Iterator[Tuple[float, int]]:
        """Iterate over (timestamp, value) tuples in insertion order."""
        for value, timestamp in self._data.items():
            yield (timestamp, value)

    def __bool__(self) -> bool:
        """Return True if the set is not empty."""
        return bool(self._data)

    def clear(self) -> None:
        """Remove all items from the set."""
        self._data.clear()

    def prune(self, cutoff_timestamp: float) -> int:
        """
        Remove all values with timestamps older than the cutoff.

        Args:
            cutoff_timestamp: Unix timestamp - items older than this are removed

        Returns:
            Number of items removed
        """
        to_remove = []
        for value, timestamp in self._data.items():
            if timestamp < cutoff_timestamp:
                to_remove.append(value)

        removed_count = len(to_remove)
        for value in to_remove:
            del self._data[value]

        return removed_count

    def prune_older_than(self, seconds: float) -> int:
        """
        Remove all values older than the specified number of seconds.

        Args:
            seconds: Remove items older than this many seconds from now

        Returns:
            Number of items removed
        """
        cutoff = time.time() - seconds
        return self.prune(cutoff)

    def get_timestamp(self, value: int) -> Optional[float]:
        """Get the timestamp for a specific value, or None if not found."""
        return self._data.get(value)

    def items(self) -> Iterator[Tuple[float, int]]:
        """Return iterator of (timestamp, value) tuples in insertion order."""
        return iter(self)

    def values(self) -> Iterator[int]:
        """Return iterator of values in insertion order."""
        return iter(self._data.keys())

    def timestamps(self) -> Iterator[float]:
        """Return iterator of timestamps in insertion order."""
        return iter(self._data.values())

    def oldest(self) -> Optional[Tuple[float, int]]:
        """Return the oldest (timestamp, value) tuple, or None if empty."""
        if not self._data:
            return None
        value = next(iter(self._data))
        return (self._data[value], value)

    def newest(self) -> Optional[Tuple[float, int]]:
        """Return the newest (timestamp, value) tuple, or None if empty."""
        if not self._data:
            return None
        value = next(reversed(self._data))
        return (self._data[value], value)

    def __repr__(self) -> str:
        items = list(self)
        return f"TimestampOrderedSet({items})"
