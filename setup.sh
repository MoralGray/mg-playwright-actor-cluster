#!/usr/bin/env bash
set -e

echo "---------------------------------------------------------------------------"
echo "Writing .env from .env.example..."
cp -f .env.example .env
echo "---------------------------------------------------------------------------"

echo "---------------------------------------------------------------------------"
echo "OpenRouter API key is required."
echo "Get one at: https://openrouter.ai/keys"
read -r -p "Enter your OpenRouter API key (sk-or-...): " API_KEY
echo "---------------------------------------------------------------------------"

if [[ -z "$API_KEY" ]]; then
    echo "No key entered. Aborted."
    exit 1
fi
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|^OPENROUTER_TOKEN=.*|OPENROUTER_TOKEN=$API_KEY|" .env
else
    sed -i "s|^OPENROUTER_TOKEN=.*|OPENROUTER_TOKEN=$API_KEY|" .env
fi

echo "---------------------------------------------------------------------------"
echo "Wildberries phone number is required for SMS authentication."
read -r -p "Enter your phone number (+79000000000): " PHONE
echo "---------------------------------------------------------------------------"

if [[ -z "$PHONE" ]]; then
    echo "No phone entered. Aborted."
    exit 1
fi
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|^WILBERRIES_USER_DETERMINISTIC_PHONE=.*|WILBERRIES_USER_DETERMINISTIC_PHONE=$PHONE|" .env
else
    sed -i "s|^WILBERRIES_USER_DETERMINISTIC_PHONE=.*|WILBERRIES_USER_DETERMINISTIC_PHONE=$PHONE|" .env
fi

echo "---------------------------------------------------------------------------"
echo "Installing Python dependencies..."
mise run prepare
echo "---------------------------------------------------------------------------"

echo "---------------------------------------------------------------------------"
echo
echo "Setup complete!"
echo "Run: mise run wb-analytics"
echo "Run: mise run extract"
echo "Run: mise run fingerprint-check"
echo "---------------------------------------------------------------------------"
