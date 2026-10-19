from fastapi import FastAPI

from mock_providers import argentina, brazil

app = FastAPI(title="Mock Invoice Providers", version="0.1.0")
app.include_router(argentina.router)
app.include_router(brazil.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
