import pytest

from qualys_tracker.parser import ParserError, parse_portal_version_response, validate_response


def test_valid_response_parses_all_fields(valid_response):
    modules = parse_portal_version_response(valid_response)
    assert len(modules) == 9
    by_name = {m.module: m.version for m in modules}
    assert by_name["FIM"] == "1.5.1"
    assert by_name["PortalApplication"] == "2.33.0.0"


def test_normalizes_module_names_and_keeps_api_field(valid_response):
    modules = parse_portal_version_response(valid_response)
    fim = next(m for m in modules if m.module == "FIM")
    assert fim.api_field == "FIM-VERSION"
    assert fim.module == "FIM"


def test_missing_service_response_raises(missing_service_response):
    with pytest.raises(ParserError):
        validate_response(missing_service_response)


def test_missing_data_raises():
    with pytest.raises(ParserError):
        validate_response({"ServiceResponse": {"responseCode": "SUCCESS"}})


def test_missing_portal_version_raises():
    raw = {"ServiceResponse": {"responseCode": "SUCCESS", "data": [{}]}}
    with pytest.raises(ParserError):
        validate_response(raw)


def test_no_version_fields_raises():
    raw = {
        "ServiceResponse": {
            "responseCode": "SUCCESS",
            "data": [{"Portal-Version": {"NotAVersionField": "x"}}],
        }
    }
    with pytest.raises(ParserError):
        validate_response(raw)


def test_response_code_failure_raises(response_code_failure):
    with pytest.raises(ParserError):
        validate_response(response_code_failure)


def test_unknown_new_module_is_included():
    raw = {
        "ServiceResponse": {
            "responseCode": "SUCCESS",
            "data": [{"Portal-Version": {"BRAND-NEW-MODULE-VERSION": "9.9.9"}}],
        }
    }
    modules = parse_portal_version_response(raw)
    assert modules[0].module == "BRAND-NEW-MODULE"
    assert modules[0].version == "9.9.9"


def test_multiple_version_fields(valid_response):
    modules = parse_portal_version_response(valid_response)
    names = {m.module for m in modules}
    assert names == {
        "PortalApplication", "WAS", "VM", "FIM", "CM", "MDS", "CA",
        "QUESTIONNAIRE", "WAF",
    }


def test_malformed_input_not_a_dict():
    with pytest.raises(ParserError):
        validate_response(["not", "a", "dict"])


def test_data_not_a_list_raises():
    raw = {"ServiceResponse": {"responseCode": "SUCCESS", "data": "oops"}}
    with pytest.raises(ParserError):
        validate_response(raw)
