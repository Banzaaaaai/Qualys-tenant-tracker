import json
import os

import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def valid_response() -> dict:
    return load_fixture("valid_response.json")


@pytest.fixture
def valid_response_changed() -> dict:
    return load_fixture("valid_response_changed.json")


@pytest.fixture
def missing_service_response() -> dict:
    return load_fixture("missing_service_response.json")


@pytest.fixture
def response_code_failure() -> dict:
    return load_fixture("response_code_failure.json")
