"""Project persisted workflow State; Generation callbacks retain the real prompt."""

STATE_FIELDS=frozenset({
    'session_id','conversation_id','request_id','turn_identity','current_turn_from_msg_id',
    'raw_question','resolved_question','intent','confidence','route','understanding','status',
    'retrieval','gap_source','gate_reason','evidence_allowed','evidence_confidence','sufficiency',
    'maintenance_reason','control','pending_decision','tool_failure','ticket_confirmation',
    'selected_order_id','selection','order','final_text','budget','continuation_of',
    'expansion_queries','expansion_fallback','evidence_checked_hash','usage_segments',
})


def project_workflow_payload(*,data,**kwargs):
    if isinstance(data,dict):
        if 'messages' in data and any(k in data for k in ('raw_question','message_refs','history_context')):
            projected={key:project_workflow_payload(data=value) for key,value in data.items() if key in STATE_FIELDS}
            projected['persisted_message_count']=len(data['messages']) if isinstance(data['messages'],list) else None
            return projected
        return {key:project_workflow_payload(data=value) for key,value in data.items()}
    if isinstance(data,(list,tuple)):
        return [project_workflow_payload(data=value) for value in data]
    return data
