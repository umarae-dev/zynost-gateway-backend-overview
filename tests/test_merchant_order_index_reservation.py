"""Regression test for a real, previously-unprotected race condition: two
concurrent checkout requests for the same merchant used to read
merchant.next_evm_index and increment it in plain Python memory, with no
DB-level locking and no unique constraint on (merchant_id,
evm_derivation_index) - both could read the same index before either
committed, derive the SAME evm_address, and silently mix two different
customers' payments together. Fixed via an atomic UPDATE...RETURNING (see
merchant_service._reserve_next_evm_index), which Postgres serializes at
the row level with zero application-side locking - plus a defense-in-depth
unique constraint added directly in production.
"""
import asyncio
import uuid
from types import SimpleNamespace

from app.services import merchant_service


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """execute() returns the POST-increment value exactly as Postgres's
    own UPDATE...RETURNING computes it server-side in one atomic
    statement - the function under test never reads-then-writes in
    application code, so there's nothing here to race."""

    def __init__(self, post_increment_value):
        self.post_increment_value = post_increment_value
        self.executed_statements = []

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        return _FakeResult(self.post_increment_value)


def _merchant(**overrides):
    base = dict(id=uuid.uuid4(), next_evm_index=999)  # stale on purpose - DB's value must win
    base.update(overrides)
    return SimpleNamespace(**base)


def test_reserve_next_evm_index_returns_the_pre_increment_value():
    merchant = _merchant()
    db = _FakeSession(post_increment_value=43)

    index = asyncio.run(merchant_service._reserve_next_evm_index(db, merchant))

    assert index == 42


def test_reserve_next_evm_index_syncs_the_in_memory_merchant_object():
    merchant = _merchant(next_evm_index=999)  # deliberately stale/wrong
    db = _FakeSession(post_increment_value=43)

    asyncio.run(merchant_service._reserve_next_evm_index(db, merchant))

    assert merchant.next_evm_index == 43


def test_reserve_next_evm_index_issues_an_update_not_a_select():
    # The whole point of the fix - a plain SELECT-then-write in Python is
    # exactly the pattern that raced. Asserting the statement type here
    # keeps a future refactor from silently reintroducing it.
    from sqlalchemy.sql.dml import Update

    merchant = _merchant()
    db = _FakeSession(post_increment_value=1)

    asyncio.run(merchant_service._reserve_next_evm_index(db, merchant))

    assert isinstance(db.executed_statements[0], Update)


def test_two_sequential_reservations_never_return_the_same_index():
    merchant = _merchant()
    db = _FakeSession(post_increment_value=101)
    first = asyncio.run(merchant_service._reserve_next_evm_index(db, merchant))

    db.post_increment_value = 102
    second = asyncio.run(merchant_service._reserve_next_evm_index(db, merchant))

    assert first == 100
    assert second == 101
    assert first != second
