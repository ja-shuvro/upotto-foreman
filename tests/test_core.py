import pytest
from upotto_foreman.core import greet


def test_greet_returns_expected_string():
    assert greet("World") == "Hello, World!"


def test_greet_type_error_on_non_string():
    with pytest.raises(TypeError):
        greet(123)
