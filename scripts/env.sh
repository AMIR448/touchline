# Project-only cloud logins, kept apart from any work accounts on this machine.
# Usage: source scripts/env.sh
export GH_CONFIG_DIR="$HOME/.config/gh-touchline"
export AZURE_CONFIG_DIR="$HOME/.azure-touchline"
[ -f .venv/bin/activate ] && source .venv/bin/activate
echo "touchline env: gh -> $GH_CONFIG_DIR, az -> $AZURE_CONFIG_DIR"
