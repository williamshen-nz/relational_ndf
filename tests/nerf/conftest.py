import json
import os

import pytest

_MODULE_PATH = os.path.dirname(__file__)


@pytest.fixture(scope="function")
def transforms():
    # Scope is function as transforms is mutable
    with open(os.path.join(_MODULE_PATH, "assets", "transforms.json")) as fp:
        transforms = json.load(fp)
    yield transforms
