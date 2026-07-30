# upotto-foreman

A lightweight agent foreman library.

## Installation

```bash
pip install .
```

Or for development:

```bash
pip install -e .[dev]
```

## Usage

```python
from upotto_foreman.core import greet

print(greet("World"))  # => "Hello, World!"
```

## Development

Install development dependencies:

```bash
pip install -r requirements-dev.txt
```

Run the test suite:

```bash
pytest -q
```

## License

MIT License.
