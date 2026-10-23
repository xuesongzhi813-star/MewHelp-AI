"""Order association uses only verified context or a server-consumed selection."""
import uuid

from app.demo_orders import list_demo_orders
from app.workflow.understanding import validated_order_reference


def verified_order_id(understanding, *, messages, selected_order_id=None, selection=None):
    # A client-provided string alone is never proof of a completed selection.
    if (selected_order_id and selection and selection.get('status') == 'consumed'
            and selection.get('selected_order_id') == selected_order_id
            and any(c['order_id'] == selected_order_id for c in selection.get('candidates', []))):
        return selected_order_id
    result = understanding.model_dump() if hasattr(understanding, 'model_dump') else understanding
    if not result or result.get('reference_status') == 'ambiguous':
        return None
    key, source_id = result.get('order_id'), result.get('source_message_id')
    if not key:
        return None
    current = next((m for m in reversed(messages) if m.type == 'human'), None)
    return validated_order_reference(key, source_id, question=str(current.content) if current else '', history=messages)


def prepare_order_selection(state):
    return {'selection_id': uuid.uuid4().hex, 'request_id': state['request_id'],
            'intent': state['intent'], 'question': state['resolved_question'],
            'candidates': list_demo_orders(), 'status': 'available'}
