"""Stable synthetic business data and coherent timelines without service dependencies."""
from datetime import datetime
from app.mcp_servers.common import MockBusinessData


def test_same_input_stable_and_caller_mutation_cannot_poison_cache():
    data=MockBusinessData(seed=0)
    first=data.logistics("ORD100")
    again=data.logistics("ORD100")
    assert first==again
    first["carrier"]="tampered"
    assert data.logistics("ORD100")["carrier"]!="tampered"


def test_cache_eviction_is_bounded():
    data=MockBusinessData(seed=0,max_cache=2)
    for key in ["a","b","c","d"]:
        data.warranty(key)
    assert data.cache_size<=2


def test_logistics_fixed_order_agrees_with_original_order_fixture():
    data=MockBusinessData(seed=0)
    assert data.logistics("1001")["current_status"]=="delivered"
    assert data.logistics("1003")["current_status"]=="delivered"
    assert data.logistics("1002")["current_status"] in {"picked_up","transit","out_for_delivery"}
    events=data.logistics("1001")["events"]
    dates=[datetime.fromisoformat(e["time"]) for e in events]
    assert dates==sorted(dates)
    assert events[-1]["status"]=="delivered"


def test_fixed_seed_empty_result_is_business_data():
    result=MockBusinessData(seed=31).return_progress("ORD404")
    assert result["found"] is False and result["is_demo"] is True
    assert result["reason"]


def test_mock_warranty_and_returns_have_only_coherent_statuses():
    data=MockBusinessData(seed=0)
    warranty=data.warranty("1001")
    progress=data.return_progress("1001")
    assert warranty["found"] and warranty["is_demo"]
    assert warranty["warranty_status"] in {"covered","expired","no_record"}
    assert warranty["coverage_note"]
    assert progress["found"] and progress["is_demo"] and progress["return_no"]
    assert progress["status"] in {"requested","approved","in_transit","received","refunded","rejected"}
    assert progress["next_step"]

