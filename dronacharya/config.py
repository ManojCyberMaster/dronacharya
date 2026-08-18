"""Configuration: ~/.dronacharya/config.toml (override dir with DRONACHARYA_HOME).

Cross-platform: all paths handled with pathlib; note directories are plain
absolute paths supplied by the user (per-OS examples in the default template).
"""

from __future__ import annotations

import os
import secrets
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

EMBEDDING_PRESETS = {
    "english": "BAAI/bge-small-en-v1.5",
    "multilingual": "intfloat/multilingual-e5-small",
}
EMBEDDING_DIM = 384  # both presets — fixed per KB (see ARCHITECTURE.md)


class DeploymentConfig(BaseModel):
    role: str = "standalone"  # standalone | server | client


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8317
    token: str = ""
    remote_url: str = ""  # client role: URL of the home server


class StorageConfig(BaseModel):
    backend: str = "sqlite"  # sqlite | postgres
    postgres_dsn: str = ""


class NotesConfig(BaseModel):
    directories: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=lambda: [
        ".tdl", ".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx"])
    scan: str = "poll"
    interval_seconds: int = 300


class EmbeddingsConfig(BaseModel):
    preset: str = "english"  # english | multilingual
    model: str = ""  # resolved from preset when empty

    @property
    def model_name(self) -> str:
        return self.model or EMBEDDING_PRESETS.get(self.preset, EMBEDDING_PRESETS["english"])


class RetrievalConfig(BaseModel):
    top_k: int = 8
    candidates: int = 50
    rerank: str = "on"    # on (CPU is fine) | auto (only with CUDA) | off
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    # TWO score scales, TWO thresholds — never compare across scales:
    # reranked scores are sigmoid probabilities (0..1); without a reranker
    # the RRF fusion scores are rank-based and barely mean anything.
    min_relevance: float = 0.30      # gate when the reranker is active
    min_confidence: float = 0.015    # gate for raw RRF scores (rerank=off)
    dup_threshold: float = 0.90


class LLMConfig(BaseModel):
    provider_order: list[str] = Field(
        default_factory=lambda: ["anthropic", "openai"]
    )
    # task routing: distillation is context-grounded extraction — a small
    # local model does it well and cheaply; empty = use provider_order
    distill_providers: list[str] = Field(default_factory=list)
    anthropic_model: str = "claude-opus-5"
    openai_model: str = "gpt-4o"
    ollama_url: str = ""      # e.g. http://localhost:11434/v1 (optional)
    ollama_model: str = ""
    vllm_url: str = ""        # any OpenAI-compatible endpoint, local or remote (optional)
    vllm_model: str = ""


class WebSearchConfig(BaseModel):
    """Optional self-hosted metasearch for the `dc ask` internet fallback.
    Called over HTTP only — never a code dependency (SearxNG is AGPL; running
    it as your own service imposes nothing on this codebase)."""

    searx_url: str = ""       # e.g. http://searxng:8080 (compose) / http://host:8081
    max_pages: int = 2        # top result pages fetched and given to the LLM


class ExtractionConfig(BaseModel):
    primary: str = "browser-html"  # browser-html | firecrawl | server-fetch
    firecrawl_api_key: str = ""


class PrivacyConfig(BaseModel):
    """Per-task egress policy. "any" = every configured provider (listing a
    cloud provider in provider_order is the consent). "local-only" = the
    task's chain keeps only self-hosted endpoints (ollama/vllm) — page text
    and questions can then never reach a cloud API, even on local failure."""

    distill: str = "any"    # any | local-only
    answer: str = "any"     # any | local-only  (chat, quick answers, --deeper)


class GuardrailsConfig(BaseModel):
    pii_mode: str = "redact"  # redact | block | off
    policy: str = "basic"
    # SSRF policy for server-side fetches: auto = private/LAN targets are
    # allowed on personal machines but blocked in server role | always | never
    allow_private_urls: str = "auto"


class SyncConfig(BaseModel):
    auto: bool = True
    interval_seconds: int = 600


class Config(BaseModel):
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    websearch: WebSearchConfig = Field(default_factory=WebSearchConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)


def home_dir() -> Path:
    env = os.environ.get("DRONACHARYA_HOME")
    return Path(env).expanduser() if env else Path.home() / ".dronacharya"


def config_path() -> Path:
    return home_dir() / "config.toml"


def db_path() -> Path:
    return home_dir() / "dronacharya.db"


DEFAULT_TEMPLATE = """\
# DronaCharya configuration. Docs: https://github.com/<you>/dronacharya
# This file was generated by `dc init`.

[deployment]
role = "standalone"          # standalone | server | client

[server]
host = "127.0.0.1"
port = 8317
token = "{token}"            # bearer token for the extension / API clients
remote_url = ""              # client role only: URL of your home server

[storage]
backend = "sqlite"           # server role may use "postgres"
postgres_dsn = ""

[notes]
# Directories to scan for your notes. Examples per OS:
#   WSL:      "/mnt/c/Users/YourName/Documents/ToDoLists"
#   Windows:  'C:\\Users\\YourName\\Documents\\ToDoLists'
#   mac/lin:  "~/Documents/notes"
directories = []
extensions = [".tdl", ".md", ".txt", ".pdf", ".docx", ".xlsx", ".pptx"]
scan = "poll"
interval_seconds = 300

[embeddings]
preset = "{preset}"          # english | multilingual — fixed per knowledge base
model = ""                   # advanced: override the preset model (requires `dc reembed`)

[retrieval]
top_k = 8
rerank = "on"                 # on (works on CPU) | auto (CUDA only) | off
rerank_model = "cross-encoder/ms-marco-MiniLM-L-6-v2"
min_relevance = 0.30          # reranked scores are probabilities (0..1)
min_confidence = 0.015        # raw fusion scores, only used when rerank = "off"
dup_threshold = 0.90

[llm]
# Providers are tried in order. All optional — remove what you don't use.
#   anthropic    = ANTHROPIC_API_KEY
#   openai       = OPENAI_API_KEY
#   ollama/vllm  = your own local or remote OpenAI-compatible endpoint
provider_order = ["anthropic", "openai"]
anthropic_model = "claude-opus-5"
openai_model = "gpt-4o"
ollama_url = ""              # e.g. "http://localhost:11434/v1"
ollama_model = ""
vllm_url = ""                # e.g. "http://dgx.local:8000/v1" or a rented GPU endpoint
vllm_model = ""
distill_providers = []       # e.g. ["ollama"] — small local model for distillation

[privacy]
# Per-task egress policy: "any" (default) or "local-only" (only your own
# ollama/vllm endpoints — content can never reach a cloud API for that task)
distill = "any"
answer = "any"

[websearch]
searx_url = ""               # your own SearxNG for grounded `dc ask` fallback
                             # (see docker compose --profile searxng)

[extraction]
primary = "browser-html"     # browser-html | firecrawl | server-fetch
firecrawl_api_key = ""

[guardrails]
pii_mode = "redact"          # redact | block | off
policy = "basic"
allow_private_urls = "auto"  # auto (blocked in server role) | always | never

[sync]
auto = true
interval_seconds = 600
"""


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def write_default_config(preset: str = "english", force: bool = False) -> Path:
    path = config_path()
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DEFAULT_TEMPLATE.format(token=secrets.token_urlsafe(24), preset=preset),
        encoding="utf-8",
    )
    return path
