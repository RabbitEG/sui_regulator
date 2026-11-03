"""Compatibility entry point; canonical implementation is proxy_process."""

from .proxy_process import main as main
from .proxy_process import run as run

if __name__ == "__main__":
    main()
