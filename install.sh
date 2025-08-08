#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
	echo "Run this from inside a Git repo." >&2
	exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SRC_DIR/hook/prepare-commit-msg" "$REPO_ROOT/.git/hooks/prepare-commit-msg"
chmod +x "$REPO_ROOT/.git/hooks/prepare-commit-msg"

# copy script if not present
mkdir -p "$REPO_ROOT/scripts"
if [ ! -f "$REPO_ROOT/scripts/gitaimsg.py" ]; then
	cp "$SRC_DIR/scripts/gitaimsg.py" "$REPO_ROOT/scripts/gitaimsg.py"
	chmod +x "$REPO_ROOT/scripts/gitaimsg.py"
fi

# copy example config if missing
if [ ! -f "$REPO_ROOT/.gitaimsg.toml" ] && [ -f "$SRC_DIR/.gitaimsg.example.toml" ]; then
	cp "$SRC_DIR/.gitaimsg.example.toml" "$REPO_ROOT/.gitaimsg.toml"
fi

git config gitaimsg.enabled true
echo "Installed. Edit .gitaimsg.toml to choose provider/model."

if [ "$SRC_DIR" != "$REPO_ROOT" ]; then
	echo "Cleaning up installer source: $SRC_DIR"
	rm -rf "$SRC_DIR"
fi