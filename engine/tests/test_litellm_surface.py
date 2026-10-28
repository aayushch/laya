"""Guard the LiteLLM surface the engine relies on, and the pinned release line."""

import re
from pathlib import Path

import litellm
import pytest

SYMBOLS = [
    "acompletion",
    "get_model_info",
    "token_counter",
    "get_valid_models",
    "models_by_provider",
    "model_cost",
]


@pytest.mark.parametrize("name", SYMBOLS)
def test_litellm_symbol_present(name):
    assert hasattr(litellm, name)


ENGINE = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("lock", ["requirements.lock", "requirements-dev.lock"])
def test_lock_pins_litellm_1_104(lock):
    text = (ENGINE / lock).read_text()
    match = re.search(r"^litellm==(\S+)", text, re.MULTILINE)
    assert match and match.group(1).startswith("1.104.")


def test_requirements_floor_is_1_104():
    text = (ENGINE / "requirements.txt").read_text()
    assert re.search(r"^litellm>=1\.104\.0,<2", text, re.MULTILINE)
