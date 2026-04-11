## [2026-03-30] Initial exploration

### Default entry inconsistency
- `main()` (no-subcommand) calls `from coding_agent.core.config import load_config` (env-vars only) + `asyncio.run(run_repl(config))`
- explicit `repl` subcommand uses Click options with envvar= fallback — has `--api-key`, `--model`, etc.
- `cli/repl.py::InteractiveSession._setup_agent()` creates DEAD legacy provider/tools (self.provider, self.tools, self.planner) that are NEVER used for execution — only `_pipeline_adapter` is used
- Fix: `main()` → `ctx.invoke(repl)` when no subcommand; remove dead legacy code in _setup_agent

### GitHub Copilot provider
- Copilot API is OpenAI-compatible at `https://api.githubcopilot.com`
- Auth: `Authorization: Bearer $GITHUB_TOKEN`
- `OpenAICompatProvider` already supports custom `base_url` and passes api_key to AsyncOpenAI
- CopilotProvider = thin subclass of OpenAICompatProvider with DEFAULT_BASE_URL preset
- Env var: prefer `GITHUB_TOKEN` when provider=copilot and AGENT_API_KEY not set
- Need to add "copilot" to: CLI `--provider` choices, `Config.provider` Literal, `LLMProviderPlugin.provide_llm()`

### File map
- `src/coding_agent/__main__.py` — CLI entry, create_agent(), run/repl/serve commands
- `src/coding_agent/cli/repl.py` — InteractiveSession, run_repl()
- `src/coding_agent/core/config.py` — Config model, load_config(), _ENV_MAP
- `src/coding_agent/plugins/llm_provider.py` — LLMProviderPlugin.provide_llm()
- `src/coding_agent/providers/openai_compat.py` — OpenAICompatProvider (base for Copilot)
- `src/coding_agent/agent.toml` — default config (currently provider=anthropic)
