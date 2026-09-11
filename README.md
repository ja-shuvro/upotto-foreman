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

### Automatic Failover

Enable automatic failover by setting `LLM_FAILOVER_ENABLED=true`. If the active provider fails with a retryable error (rate limit 429, quota exhaustion, auth/expired key, timeout, connection failure), Foreman automatically switches to the next configured provider.

- `LLM_FAILOVER_ENABLED` — `true` | `false` (default: `false`, opt-in)
- `LLM_PROVIDER_PRIORITY` — Comma-separated order (default: `anthropic,gemini,openrouter,agentrouter`)

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
