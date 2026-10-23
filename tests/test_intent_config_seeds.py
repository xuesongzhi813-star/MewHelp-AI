from app.config import Settings, get_settings
from app.workflow.contracts import BudgetState
from app.workflow.models import ModelGateway
from tests.test_workflow_models import Provider


def test_future_small_model_seed_is_explicit_and_not_an_execution_mode():
    settings = Settings(_env_file=None)
    assert settings.intent_small_model is None
    assert settings.intent_escalation_threshold == .7


async def test_configured_future_seed_never_selects_or_calls_small_model(monkeypatch):
    monkeypatch.setenv('INTENT_SMALL_MODEL', 'future-small-model-never-call')
    monkeypatch.setenv('INTENT_ESCALATION_THRESHOLD', '0.99')
    get_settings.cache_clear()
    primary = Provider()
    primary.parsed = {'intent': '订单', 'confidence': .8}
    try:
        gateway = ModelGateway(primary)
        decision = await gateway.classify([], BudgetState())
        assert decision.intent == '订单'
        assert gateway.intent_model is primary
        assert len(primary.calls) == 1
        assert get_settings().intent_small_model == 'future-small-model-never-call'
    finally:
        get_settings.cache_clear()
