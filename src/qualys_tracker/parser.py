"""Parses and validates the Qualys Portal Version API response.

Every `*-VERSION` field is processed dynamically -- no module name is
ever hardcoded -- so newly introduced Qualys modules are picked up
automatically.
"""

from __future__ import annotations

from .models import ModuleVersion

VERSION_SUFFIX = "-VERSION"


class ParserError(Exception):
    """The response failed structural or semantic validation.

    Raising this must never cause the caller to overwrite a known-good
    snapshot -- see state.py / main.py.
    """


def validate_response(raw: dict) -> dict:
    """Validate the envelope and return the `Portal-Version` dict.

    Checks, in order: the payload is a dict, `ServiceResponse` exists,
    `responseCode == SUCCESS`, `data` is a non-empty list, the first
    element has a `Portal-Version` dict, and it has at least one
    `*-VERSION` field.
    """
    if not isinstance(raw, dict):
        raise ParserError("Response body is not a JSON object")

    service_response = raw.get("ServiceResponse")
    if not isinstance(service_response, dict):
        raise ParserError("Missing 'ServiceResponse' in API response")

    response_code = service_response.get("responseCode")
    if response_code != "SUCCESS":
        raise ParserError(f"API responseCode was {response_code!r}, expected SUCCESS")

    data = service_response.get("data")
    if not isinstance(data, list) or not data:
        raise ParserError("Missing or empty 'ServiceResponse.data'")

    first = data[0]
    if not isinstance(first, dict):
        raise ParserError("'ServiceResponse.data[0]' is not an object")

    portal_version = first.get("Portal-Version")
    if not isinstance(portal_version, dict) or not portal_version:
        raise ParserError("Missing or empty 'Portal-Version' object")

    version_fields = {
        k: v for k, v in portal_version.items() if k.endswith(VERSION_SUFFIX)
    }
    if not version_fields:
        raise ParserError("'Portal-Version' contains no '*-VERSION' fields")

    return portal_version


def parse_portal_version_response(raw: dict) -> list[ModuleVersion]:
    """Validate `raw` and return every module's normalized version.

    Non-`*-VERSION` fields in `Portal-Version` (if Qualys ever adds
    metadata alongside the version fields) are ignored rather than
    rejected, keeping this forward-compatible with unknown fields.
    """
    portal_version = validate_response(raw)

    modules: list[ModuleVersion] = []
    for api_field, version in portal_version.items():
        if not api_field.endswith(VERSION_SUFFIX):
            continue
        module_name = api_field[: -len(VERSION_SUFFIX)]
        modules.append(
            ModuleVersion(
                api_field=api_field,
                module=module_name,
                version=str(version),
            )
        )
    return modules
