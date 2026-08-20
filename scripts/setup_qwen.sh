#!/usr/bin/env bash
set -euo pipefail

# Run this on the Uno Q Linux terminal, not on the STM32 sketch side.
# Qwen stays local and is exposed only through localhost.
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
fi

ollama pull qwen3:0.6b
echo "Qwen is installed. Start the local service with: ollama serve"
echo "Then start Spidey; the website chat will use http://127.0.0.1:11434."
