"""A checkpoint nonce alone must never authorize a second business write."""
from app.tooling.capabilities import CapabilityStore


def test_capability_is_bound_and_consumed_atomically():
    store=CapabilityStore()
    nonce=store.issue("s","call","args","policy")
    assert not store.consume(nonce,"other","call","args")
    assert not store.consume(nonce,"s","call","changed")
    assert store.consume(nonce,"s","call","args",policy_hash="policy")
    assert not store.consume(nonce,"s","call","args",policy_hash="policy")


def test_policy_change_does_not_consume_another_valid_token():
    store=CapabilityStore()
    nonce=store.issue("s","call","args","policy")
    assert not store.consume(nonce,"s","call","args",policy_hash="changed")
    assert store.consume(nonce,"s","call","args",policy_hash="policy")


def test_restart_loses_execution_capability():
    store=CapabilityStore()
    nonce=store.issue("s","call","args","policy")
    store.clear()
    assert not store.consume(nonce,"s","call","args")


def test_capability_store_has_bounded_capacity():
    store=CapabilityStore(max_entries=1)
    first=store.issue("s","one","args","policy")
    store.issue("s","two","args","policy")
    assert not store.consume(first,"s","one","args")

