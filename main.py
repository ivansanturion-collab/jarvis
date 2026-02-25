"""Jarvis — Entry point."""

from src.config import validate_config
from src.telegram_bot import run_bot


def main():
    validate_config()
    run_bot()


if __name__ == "__main__":
    main()
