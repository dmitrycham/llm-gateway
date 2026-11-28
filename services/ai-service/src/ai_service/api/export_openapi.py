"""Print the FastAPI OpenAPI schema as JSON for the contract drift check (ADR-005).

Run with ``python -m ai_service.api.export_openapi [output-path]``; no path means stdout.
"""

import json
import sys
from pathlib import Path
from typing import Any

from ai_service.api.app import create_app
from ai_service.api.health import ReadinessChecks
from ai_service.api.settings import Settings


async def _never_called() -> bool:  # pragma: no cover - the export runs no request
    return False


def build_schema() -> dict[str, Any]:
    # Explicit settings and checks: the export must not depend on the caller's environment.
    app = create_app(
        Settings(),
        ReadinessChecks(postgres=_never_called, gateway=_never_called),
    )
    return app.openapi()


def main(argv: list[str]) -> None:
    text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main(sys.argv)
