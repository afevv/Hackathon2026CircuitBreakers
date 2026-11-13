import pytest

from ci_assistant.config import Settings
from ci_assistant.container import build_container


@pytest.fixture()
def container():
    return build_container(Settings())  # default: sample data + stub LLM
