"""Recover the evaluated turn, never re-run retrieval or use a later turn."""
import asyncio
from copy import deepcopy
import logging
from dataclasses import asdict

from app.flywheel.contracts import TurnIdentity, EvidenceSnapshot


logger = logging.getLogger(__name__)


def snapshot_from_retrieval(identity, retrieval):
    if retrieval is not None and not isinstance(retrieval, dict):
        retrieval = {'hits':[asdict(hit) for hit in retrieval.hits], 'strategy':retrieval.strategy}
    status = 'not_retrieved' if retrieval is None else ('retrieved' if retrieval.get('hits') else 'no_hits')
    return EvidenceSnapshot(identity, status, deepcopy((retrieval or {}).get('hits', [])[:5]),
        retrieval_strategy=(retrieval or {}).get('strategy'), profile_version=(retrieval or {}).get('profile_version'))


def read_snapshot(value):
    if value is None or isinstance(value, list):
        return EvidenceSnapshot(None, 'legacy_unknown', deepcopy(value[:5]) if value else [])
    if not isinstance(value, dict):
        return EvidenceSnapshot(None, 'snapshot_unavailable', [])
    try:
        identity = TurnIdentity(**value['identity']) if value.get('identity') else None
    except (TypeError, KeyError):
        identity = None
    return EvidenceSnapshot(identity, value.get('snapshot_status', 'legacy_unknown'),
        deepcopy(value.get('hits', [])[:5]), deepcopy(value.get('processing', {})),
        schema_version=value.get('schema_version',1),retrieval_strategy=value.get('retrieval_strategy'),
        profile_version=value.get('profile_version'))


def _matches(state, identity):
    if state.get('session_id') != identity.session_id:
        return False
    if state.get('conversation_id') != identity.conversation_id or state.get('current_turn_from_msg_id') != identity.user_message_id:
        return False
    stored = state.get('turn_identity') or {}
    if stored.get('assistant_message_id') is not None:
        return stored['assistant_message_id'] == identity.assistant_message_id
    return any(ref.get('db_id') == identity.assistant_message_id
               and ref.get('turn_from_msg_id') == identity.user_message_id
               for ref in state.get('message_refs', {}).values())


async def _history_snapshot(runtime, identity):
    current = await runtime.get_state(identity.session_id)
    if _matches(current, identity):
        return current
    async for snapshot in runtime.graph().aget_state_history(runtime.config(identity.session_id)):
        if _matches(snapshot.values, identity):
            return snapshot.values
    return None


async def snapshot_for_turn(runtime, identity):
    try:
        async with asyncio.timeout(5):
            state = await _history_snapshot(runtime, identity)
        if state is not None:
            raw = state.get('turn_identity')
            original = TurnIdentity(**raw) if raw else identity
            return snapshot_from_retrieval(original, state.get('retrieval'))
    except Exception as exc:
        logger.warning('feedback snapshot unavailable exception=%s', type(exc).__name__)
    return EvidenceSnapshot(identity, 'snapshot_unavailable', [])
