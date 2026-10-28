"""Catch coercion, missing constraints, unsafe references and leaking parameter values."""
import math
import pytest
from app.tooling.contracts import ToolDefinition
from app.tooling.validation import validate_arguments,validate_definition


SCHEMA={"type":"object","properties":{
    "n":{"type":"integer","minimum":1,"maximum":3},
    "kind":{"enum":["售后","投诉"]},
    "text":{"type":"string","minLength":1,"maxLength":5}},
    "required":["n","kind"],"additionalProperties":False}


@pytest.mark.parametrize("args,path",[
    ({"n":True,"kind":"售后"},"$.n"),({"n":"2","kind":"售后"},"$.n"),
    ({"kind":"售后"},"$"),({"n":0,"kind":"售后"},"$.n"),
    ({"n":4,"kind":"售后"},"$.n"),({"n":1,"kind":"咨询"},"$.kind"),
    ({"n":1,"kind":"售后","text":""},"$.text"),
    ({"n":1,"kind":"售后","text":"abcdef"},"$.text"),
    ({"n":1,"kind":"售后","confirmed":True},"$"),([], "$"),
])
def test_invalid_arguments_return_field_constraints(args,path):
    errors=validate_arguments(SCHEMA,args)
    assert errors and errors[0]["path"] == path
    assert len(errors)<=5


def test_valid_parameters_and_local_reference_work():
    assert validate_arguments(SCHEMA,{"n":2,"kind":"售后"}) == ()
    schema={"type":"object","properties":{"n":{"$ref":"#/$defs/number"}},
            "$defs":{"number":{"type":"integer","minimum":1}}}
    assert validate_arguments(schema,{"n":1}) == ()
    assert validate_arguments(schema,{"n":0})


@pytest.mark.parametrize("number",[math.inf,-math.inf,math.nan])
def test_non_finite_json_never_accepted(number):
    assert validate_arguments({"type":"object","properties":{"n":{"type":"number"}}},{"n":number})


def test_error_feedback_never_echoes_argument_values():
    errors=validate_arguments(SCHEMA,{"n":"private-value","kind":"secret-enum"})
    assert "private-value" not in str(errors)
    assert "secret-enum" not in str(errors)


@pytest.mark.parametrize("schema",[
    {"type":"object","properties":{"x":{"$ref":"http://127.0.0.1:9/secret"}}},
    {"type":"object","properties":{"x":{"$dynamicRef":"https://example.org/schema"}}},
    {"type":"array"},
    {"$schema":"http://json-schema.org/draft-07/schema#","type":"object"},
    {"type":"object","properties":{"x":{"$ref":"#/$defs/missing"}}},
])
def test_unsafe_or_unresolvable_definition_is_rejected(schema):
    with pytest.raises(ValueError):
        validate_definition(ToolDefinition(name="lookup",description="用途",input_schema=schema,source="vendor",remote_name="lookup"))


def test_reserved_system_args_cannot_enter_open_external_schema():
    errors=validate_arguments({"type":"object"},{"conversation_id":42,"confirmed":True})
    assert errors

