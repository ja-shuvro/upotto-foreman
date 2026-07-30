"""Core utilities for upotto_foreman."""


def greet(name: str) -> str:
    """Return a friendly greeting.

    Args:
        name: The name to greet.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    return f"Hello, {name}!"
