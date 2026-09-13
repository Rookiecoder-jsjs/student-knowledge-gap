"""Local-only router capacity smoke test; no real upstream is contacted."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from llm_router.main import RouterConfig, create_app


async def main(total: int = 32) -> None:
    async def mock_upstream(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return httpx.Response(200, json={"id": "mock", "choices": [{"message": {"content": "ok"}}]})

    app = create_app(
        RouterConfig(
            "https://mock.invalid/v1",
            ("mock-key-a", "mock-key-b"),
            "local-token",
            max_concurrency=8,
            text_concurrency=4,
            key_concurrency=2,
        ),
        transport=httpx.MockTransport(mock_upstream),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://router") as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer local-token"},
                    json={"model": "mock", "messages": [{"role": "user", "content": str(i)}]},
                )
                for i in range(total)
            )
        )
    print(json.dumps({"total": total, "status_counts": {str(code): sum(r.status_code == code for r in responses) for code in sorted({r.status_code for r in responses})}}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 32))
