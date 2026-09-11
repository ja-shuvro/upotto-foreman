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

## LLM Providers

Upotto Foreman supports four LLM provider backends configured via `LLM_PROVIDER`:

- `anthropic` (default) — Direct Anthropic API (`ANTHROPIC_API_KEY`)
- `openrouter` — OpenRouter unified gateway (`OPENROUTER_API_KEY`)
- `agentrouter` — AgentRouter relay (`AGENTROUTER_API_KEY`, `AGENTROUTER_BASE_URL`)
- `gemini` — Google AI Studio Gemini API (`GEMINI_API_KEY` or `GOOGLE_API_KEY`, model via `GEMINI_MODEL`, e.g. `gemini-2.0-flash`)

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
