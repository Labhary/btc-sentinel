"""Fail-closed gate for the not-yet-integrated production paper runtime."""

from __future__ import annotations

import os


def main() -> int:
    enabled = os.environ.get("PAPER_ENGINE_ENABLED", "false")
    if enabled not in {"true", "false"}:
        raise SystemExit("PAPER_ENGINE_ENABLED must be exactly true or false")
    if enabled == "false":
        print("Paper engine is disabled; readiness checks only.")
        return 0
    raise SystemExit(
        "Activation refused: the typed D1 repository adapter and atomic signal/outbox "
        "commit are not implemented."
    )


if __name__ == "__main__":
    raise SystemExit(main())
