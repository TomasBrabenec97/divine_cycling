import json
from typing import ClassVar

import pytest

from app.importers import road_worlds_2026


def test_empty_uci_fallback_has_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}

        def raise_for_status(self) -> None:
            pass

        def json(self) -> None:
            raise json.JSONDecodeError("empty", "", 0)

    class CompletedProcess:
        stdout = '""'

    monkeypatch.setattr(road_worlds_2026.httpx, "post", lambda *args, **kwargs: Response())
    monkeypatch.setattr(
        road_worlds_2026.subprocess, "run", lambda *args, **kwargs: CompletedProcess()
    )

    with pytest.raises(RuntimeError, match="UCI returned no usable ranking data"):
        road_worlds_2026.fetch_uci_rankings()
