# How to configure LLM providers

zero-pipeline uses an LLM to discover API configurations, generate auth documentation, plan API calls, and heal failures. You bring your own API key.

## Supported providers

| Provider | Key | Covers |
|----------|-----|--------|
| **OpenAI-compatible** (default) | `OPENAI_API_KEY` or keychain | OpenAI, Azure OpenAI, Groq, Together, Mistral, DeepSeek, OpenRouter, Ollama, vLLM, LM Studio |
| **Anthropic** | `ANTHROPIC_API_KEY` or keychain | Claude models via the Anthropic Messages API |

## Store your API key

Keys are stored in the OS keychain (macOS Keychain / Linux Secret Service):

```bash
# OpenAI
pipeline auth set openai --token sk-proj-...

# Anthropic
pipeline auth set anthropic --token sk-ant-...
```

For CI or environments without a keychain, set the well-known env var instead:

```bash
export OPENAI_API_KEY=sk-proj-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

Resolution order: keychain first, then env var.

## Switch providers

Set `PIPELINE_LLM_PROVIDER` to select your provider:

```bash
# Use Anthropic instead of OpenAI
export PIPELINE_LLM_PROVIDER=anthropic
```

Or in a `.env` file at the project root:

```
PIPELINE_LLM_PROVIDER=anthropic
```

## Override the model

```bash
# Use a specific OpenAI model
export PIPELINE_LLM_MODEL=gpt-4o-mini

# Use a specific Claude model
export PIPELINE_LLM_PROVIDER=anthropic
export PIPELINE_LLM_MODEL=claude-sonnet-4-20250514
```

Default models when unset:
- OpenAI: `gpt-4o`
- Anthropic: `claude-sonnet-4-20250514`

## Use a local model (Ollama)

No API key needed for local inference:

```bash
# Start Ollama
ollama serve

# Point zero-pipeline at it
export PIPELINE_LLM_PROVIDER=openai
export PIPELINE_LLM_BASE_URL=http://localhost:11434
export PIPELINE_LLM_MODEL=llama3.2
```

The `Authorization` header is omitted automatically when no key is configured and a custom base URL is set.

## Use Azure OpenAI

```bash
export PIPELINE_LLM_PROVIDER=openai
export PIPELINE_LLM_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment
export PIPELINE_LLM_MODEL=gpt-4o
pipeline auth set openai --token your-azure-api-key
```

## Use Groq, Together, or other OpenAI-compatible services

```bash
# Groq
export PIPELINE_LLM_BASE_URL=https://api.groq.com/openai
export PIPELINE_LLM_MODEL=llama-3.3-70b-versatile
pipeline auth set openai --token gsk_...

# Together AI
export PIPELINE_LLM_BASE_URL=https://api.together.xyz
export PIPELINE_LLM_MODEL=meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo
pipeline auth set openai --token ...
```

## Use vLLM or LM Studio

```bash
# vLLM
export PIPELINE_LLM_BASE_URL=http://localhost:8000
export PIPELINE_LLM_MODEL=your-model-name

# LM Studio
export PIPELINE_LLM_BASE_URL=http://localhost:1234
export PIPELINE_LLM_MODEL=your-model-name
```

No API key needed for local servers.

## Verify your setup

```bash
# Check if the LLM provider is reachable
pipeline chat "hello"
```

If the provider is misconfigured, you'll see:

```
LLM not available. Run: pipeline auth set <provider>
```

## See also

- [Reference: configuration options](../reference.md#llm-provider-settings)
- [Tutorial: build your first pipeline](../tutorial.md)

---

### Validation checklist

**Pre-hook (before writing):**
- [ ] Is the task framed as a specific problem to solve?
- [ ] Are assumptions stated (e.g. familiarity with LLM concepts)?

**Post-hook (after writing):**
- [ ] Can a user with assumed knowledge follow every step without confusion?
- [ ] Are all variations and alternatives noted without cluttering the main flow?
