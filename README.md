# GitAiMsg — LLM-powered Git commit messages

**GitAiMsg** automatically drafts your commit message using an AI model based on your staged changes.
It runs as a `prepare-commit-msg` hook, **only** when no `-m` message is supplied.

Features:
- Works with **Ollama** (local), **OpenAI**, and **Gemini**
- Writes Conventional Commits–style messages
- Never blocks a commit (fails silently on errors)
- Configurable via `.gitaimsg.toml` or environment variables

## 📦 Installation

Clone this repository somewhere accessible:

```bash
git clone https://github.com/richardhedges/gitaimsg.git
```

Inside the Git repo where you want AI commit messages:

```bash
chmod +x gitaimsg/install.sh
./gitaimsg/install.sh
```

On Windows PowerShell:

```powershell
..\gitaimsg\install.ps1
```

This will:
- Install `prepare-commit-msg` into `.git/hooks`
- Copy the generator script into `scripts/ai_commit_msg.py`
- Drop an example `.gitaimsg.toml` config into your repo root

## ⚙️ Configuration

Create `.gitaimsg.toml` in your repo root (or edit the installed example):

```toml
[gitaimsg]
provider = "ollama" # "ollama" | "openai" | "gemini"
model = "qwen2.5-coder:7b"
max_diff_chars = 15000
timeout_s = 30
temperature = 0.2
top_p = 1.0
system_prompt = "You are a senior developer writing concise Conventional Commit messages."

[provider.ollama]
base_url = "http://127.0.0.1:11434"

[provider.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[provider.gemini]
api_key_env = "GEMINI_API_KEY"
```

You can also create a **user-level config** at:

```
~/.config/gitaimsg/config.toml
```

Repo config overrides user config.

## 🌐 Environment Variable Overrides

Any config option can be overridden via environment variables. Useful for CI or quick switches.

```bash
export GITAIMSG_PROVIDER=openai
export GITAIMSG_MODEL=gpt-4o-mini
export OPENAI_API_KEY=sk-...
export OLLAMA_URL=http://127.0.0.1:11434
```

## 🚦 Usage

- Run `git commit` normally.
- If you don’t pass `-m`, the hook will:
1. Collect staged file list + diff
2. Send to your configured LLM provider
3. Write the returned message into the commit message buffer
- Your editor will still open so you can tweak it before saving.

**Skip for a single commit:**
```bash
GITAIMSG_DISABLE=1 git commit
```

**Disable globally:**
```bash
git config gitaimsg.enabled false
```

## 🛠 Requirements

- Git
- Python 3.11+ uses stdlib `tomllib` (For Python 3.8–3.10: `pip install tomli`)
- Network access for remote LLMs (OpenAI, Gemini)
- Running Ollama server for local use

## ❌ Uninstall

```bash
git config --unset gitaimsg.enabled || true
rm -f .git/hooks/prepare-commit-msg
```

## 📝 Notes

Skips when:
- Message is provided with `-m` or `-F`
- Commit is a merge, squash, or from a template
- There are no staged changes
- Truncates large diffs for performance
- If the LLM call fails, commit proceeds as normal

## 📄 License

MIT LICENSE
