"""Reads ANTHROPIC_API_KEY strictly from this project's own .env file.

Deliberately does NOT fall back to (or merge with) ambient/system environment
variables -- this project is self-contained, so a key set in the user's shell
profile or elsewhere on the machine must never be picked up here.
"""
import os

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")


def _read_env_file() -> dict:
    values = {}
    if not os.path.exists(ENV_PATH):
        return values
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_api_key() -> str:
    key = _read_env_file().get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            f"ANTHROPIC_API_KEY not set in this project's .env file ({ENV_PATH}). "
            "Copy .env.example to .env and paste your key there."
        )
    return key
