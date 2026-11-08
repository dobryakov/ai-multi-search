from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_api_key(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    allowed = settings.api_key_set()
    if not allowed:
        # No keys configured → open (useful for local tests). Production must set API_KEYS.
        return "anonymous"

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    if token not in allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
