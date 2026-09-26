# Model providers

LiteAgents sends shared model calls through the LiteLLM Python SDK. Choose a
`provider/model` name and set the provider's credentials. The harness choice is
independent: an Anthropic model can run in DeepAgents, and an OpenAI model can
run in the Claude harness.

These are configuration examples, not a claim that every provider/model
combination was live-tested. Use a model available to your account, with tool
calling and other features your agent needs. See [validation](validation.md)
for tested behavior and [LiteLLM's provider directory](https://docs.litellm.ai/docs/providers)
for additional providers and their model availability.

## OpenAI

```sh
export OPENAI_API_KEY="your-openai-key"
```

```python
profile = ProfileOptions(harness="deepagents", model="openai/gpt-5.4-mini", tools=[])
```

## Anthropic

```sh
export ANTHROPIC_API_KEY="your-anthropic-key"
```

```python
profile = ProfileOptions(harness="deepagents", model="anthropic/claude-sonnet-4-6", tools=[])
```

## OpenRouter

```sh
export OPENROUTER_API_KEY="your-openrouter-key"
```

```python
profile = ProfileOptions(
    harness="deepagents", model="openrouter/anthropic/claude-sonnet-4.6", tools=[],
)
```

OpenRouter's model ID follows its provider name. No `api_base` is needed;
LiteLLM knows the OpenRouter endpoint.

## More API-key providers

Set the key and use the model string from the table. These use the same
`ProfileOptions(harness="deepagents", model=..., tools=[])` and `query()` interface.

| Provider | Example `model` | Setup |
| --- | --- | --- |
| Google Gemini | `gemini/gemini-2.5-flash` | `export GEMINI_API_KEY="your-key"` |
| Groq | `groq/llama-3.3-70b-versatile` | `export GROQ_API_KEY="your-key"` |
| Mistral | `mistral/mistral-small-latest` | `export MISTRAL_API_KEY="your-key"` |
| DeepSeek | `deepseek/deepseek-chat` | `export DEEPSEEK_API_KEY="your-key"` |
| Together AI | `together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo` | `export TOGETHERAI_API_KEY="your-key"` |
| xAI | `xai/grok-3-mini` | `export XAI_API_KEY="your-key"` |

## Azure OpenAI

Use your **deployment name**, Azure endpoint, and a supported API version:

```sh
export AZURE_API_KEY="your-azure-key"
export AZURE_API_BASE="https://your-resource.openai.azure.com/"
export AZURE_API_VERSION="2024-10-21"
```

```python
profile = ProfileOptions(harness="deepagents", model="azure/your-deployment-name", tools=[])
```

Choose the API version required by your deployment. Keep these settings in
Azure's environment variables; you do not need `model_kwargs.api_base`.
See [LiteLLM's Azure guide](https://docs.litellm.ai/docs/providers/azure).

## Amazon Bedrock

Install `boto3` if it is not already available. Use your application's AWS
credential chain (for example an IAM role or local AWS profile) and region:

```sh
python -m pip install boto3
export AWS_REGION_NAME="us-east-1"
```

```python
profile = ProfileOptions(
    harness="deepagents",
    model="bedrock/your-model-or-inference-profile-id",
    tools=[],
)
```

Use a model or inference profile enabled in your account and region. Temporary
credentials also require `AWS_SESSION_TOKEN` alongside `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY`. Do not copy credentials into notebook source.
See [LiteLLM's Bedrock guide](https://docs.litellm.ai/docs/providers/bedrock).

## Google Vertex AI

Use Google Application Default Credentials with permission to invoke your model:

```sh
python -m pip install google-auth
gcloud auth application-default login
export VERTEXAI_PROJECT="your-project-id"
export VERTEXAI_LOCATION="us-central1"
```

```python
profile = ProfileOptions(harness="deepagents", model="vertex_ai/gemini-2.5-flash", tools=[])
```

In a deployment, use the workload's service account or an appropriate credential
file via `GOOGLE_APPLICATION_CREDENTIALS` instead of an interactive login.
See [LiteLLM's Vertex AI guide](https://docs.litellm.ai/docs/providers/vertex).

## Local models with Ollama

Start Ollama and pull a tool-capable model such as `llama3.1`, then configure:

```sh
export OLLAMA_API_BASE="http://localhost:11434"
```

```python
profile = ProfileOptions(harness="deepagents", model="ollama_chat/llama3.1", tools=[])
```

Ollama must be reachable from the process running the agent. In Colab,
`localhost` is the cloud runtime, not your laptop. Model speed and capabilities
depend on the selected model and the machine hosting it.
See [LiteLLM's Ollama guide](https://docs.litellm.ai/docs/providers/ollama).

## Optional LiteLLM gateway

Use the gateway's exact model alias, endpoint, and key. Your application does
not need the underlying provider keys. No environment variable is required:

```python
from getpass import getpass

profile = ProfileOptions(
    harness="deepagents",
    model="my-model",
    model_kwargs={
        "api_base": "https://your-gateway.example/v1",
        "api_key": getpass("Gateway API key: "),
    },
    tools=[],
)
```

The gateway owns provider credentials and routing. Its model alias is sent
unchanged, including slashes; no extra prefix is needed. Without a gateway,
LiteLLM routes `provider/model` directly to the provider.
To deploy a gateway, follow the
[LiteLLM Gateway quickstart](https://docs.litellm.ai/docs/proxy/docker_quick_start).

## In Colab

For the API-key providers above, store the named key in Colab **Secrets** and
enable notebook access. The cookbooks read it for you, or ask in a hidden prompt.

Azure needs its endpoint and API version in the runtime environment as well.
Bedrock and Vertex AI need their cloud authentication configured inside the
runtime. For example, authorized Google users can initialize Colab authentication
with `from google.colab import auth; auth.authenticate_user()`, then set the
project and region. Your laptop's credentials are not inherited.

For non-secret settings, a cell such as
`os.environ["VERTEXAI_PROJECT"] = "your-project-id"` is sufficient.
Read additional credentials from Secrets using `userdata.get(...)` and place
them in the matching environment variables without printing their values.
