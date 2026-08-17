# Installing the `dc` CLI (client machines)

`dc` runs on macOS, Linux, Windows native, and WSL. The recommended installer
is [uv](https://docs.astral.sh/uv/) (single binary, no Python setup needed).

## Linux / WSL / macOS

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # install uv (once)
uv tool install "dronacharya[server] @ git+https://github.com/<you>/dronacharya.git"
# or from a local clone:
#   git clone <repo> && uv tool install "./dronacharya[server]"
dc init                    # creates ~/.dronacharya/config.toml + the KB
```

## Windows (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install "dronacharya[server] @ git+https://github.com/<you>/dronacharya.git"
dc init
```

Notes:
- For LLM answers set `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, or point
  `[llm].ollama_url`/`[llm].vllm_url` at your own model server — see
  [hardware/](hardware/README.md). Capture and search need no LLM at all.
- Once published to PyPI this becomes `uv tool install dronacharya`.
- Upgrade later with `uv tool upgrade dronacharya` (or re-run the install
  command for git sources).

## Connect to your home server (optional)

Edit `~/.dronacharya/config.toml` (Windows: `C:\Users\<you>\.dronacharya\config.toml`):

```toml
[deployment]
role = "client"

[server]
remote_url = "http://<server-host>:8317"
token = "<the token from the server's config>"
```

Then `dc sync` — your device stays fully usable offline and reconciles with
the server whenever you sync.

## First knowledge

```bash
dc seed install <path-or-url>/cli-essentials.dckit.json   # starter knowledge
dc "command line for mounting local windows drive in wsl" # just ask
```

`dc "<question>"` is the quick mode: it answers with the command line and one
usage example only. If your KB doesn't know, it searches the internet, shows
the qualified answer with its source, and embeds it into your KB (asking you
first when confidence is low).
