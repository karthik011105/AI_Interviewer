"""One translation boundary from pymongo's errors into the domain hierarchy.

Why this exists
---------------
Every route module catches ``DatabaseClientError`` — 46 handlers across seven of
them. But ``MongoRepository`` calls pymongo directly in roughly forty places,
and ``routes_auth`` reaches ``repo.db.users`` directly too, so a raw
``PyMongoError`` escaped all of those handlers. Measured in Phase 1.8:
``AutoReconnect`` (what an Atlas failover produces) and ``ExecutionTimeout``
(a slow query on a shared-tier cluster) each surfaced as a **500 instead of a
503**. That difference decides whether a client retries, and both events are
expected on Atlas M0 rather than exotic.

Why a proxy rather than 46 edits
--------------------------------
Editing the handlers would be the wrong shape: the gap is that the data layer
leaks a foreign exception type, so the fix belongs where that leak happens.
``MongoRepository.db`` returns a proxy that wraps every collection operation, so
one place covers all forty-odd call sites *and* the direct ``repo.db.users``
access in the auth routes, without touching a single query.

Cursors are wrapped too. ``find()`` returns immediately and the server work
happens during iteration, so a failure mid-iteration would otherwise bypass the
translation entirely — which is exactly the failover case.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    PyMongoError,
    ServerSelectionTimeoutError,
    WTimeoutError,
)

from .db_errors import (
    DatabaseClientError,
    DatabaseDependencyError,
    DuplicateRecordError,
)

# Connectivity and timeout failures are transient and worth retrying, so they
# map to the dependency error the routes already turn into a 503.
_TRANSIENT = (
    AutoReconnect,
    ConnectionFailure,
    ExecutionTimeout,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WTimeoutError,
)


def translate_driver_error(exc: PyMongoError) -> DatabaseClientError:
    """Map a pymongo exception onto this package's error hierarchy."""

    if isinstance(exc, DuplicateKeyError):
        return DuplicateRecordError(f"A record violating a unique index already exists: {exc}")
    if isinstance(exc, _TRANSIENT):
        return DatabaseDependencyError(f"MongoDB is unavailable: {exc}")
    return DatabaseClientError(f"MongoDB operation failed: {exc}")


class _TranslatingCursor:
    """Wraps a cursor so a failure during iteration is translated too."""

    __slots__ = ("_cursor",)

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def __iter__(self) -> Iterator[Any]:
        try:
            for document in self._cursor:
                yield document
        except PyMongoError as exc:
            raise translate_driver_error(exc) from exc

    def __getattr__(self, name: str) -> Any:
        # sort(), limit(), skip() and friends return the cursor itself, so
        # re-wrap whatever comes back to keep the chain translated.
        attribute = getattr(self._cursor, name)
        if not callable(attribute):
            return attribute

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                result = attribute(*args, **kwargs)
            except PyMongoError as exc:
                raise translate_driver_error(exc) from exc
            return _TranslatingCursor(result) if _is_cursor(result) else result

        return _wrapped


def _is_cursor(value: Any) -> bool:
    # Duck-typed rather than isinstance, to cover Cursor and CommandCursor
    # without importing both and without caring which pymongo returns.
    return hasattr(value, "__iter__") and hasattr(value, "next") and not isinstance(
        value, (str, bytes, list, dict)
    )


class _TranslatingCollection:
    __slots__ = ("_collection",)

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._collection, name)
        if not callable(attribute):
            return attribute

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                result = attribute(*args, **kwargs)
            except PyMongoError as exc:
                raise translate_driver_error(exc) from exc
            return _TranslatingCursor(result) if _is_cursor(result) else result

        return _wrapped


class TranslatingDatabase:
    """A pymongo Database whose collections raise domain errors.

    Deliberately not a subclass: pymongo's Database does a lot in ``__init__``
    and intercepting attribute access is the whole point.
    """

    __slots__ = ("_database",)

    def __init__(self, database: Any) -> None:
        self._database = database

    def __getitem__(self, name: str) -> _TranslatingCollection:
        return _TranslatingCollection(self._database[name])

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._database, name)
        # db.users -> a collection; db.command / db.list_collection_names -> a
        # method that still needs its errors translated.
        if _is_collection(attribute):
            return _TranslatingCollection(attribute)
        if not callable(attribute):
            return attribute

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                result = attribute(*args, **kwargs)
            except PyMongoError as exc:
                raise translate_driver_error(exc) from exc
            return _TranslatingCursor(result) if _is_cursor(result) else result

        return _wrapped


def _is_collection(value: Any) -> bool:
    return hasattr(value, "find_one") and hasattr(value, "insert_one")


__all__ = ["TranslatingDatabase", "translate_driver_error"]
