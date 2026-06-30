from fastapi import APIRouter

# We don't use a prefix here because we want this at the absolute root "/"
system_router = APIRouter(tags=["System"])


@system_router.get("/")
def read_root() -> dict[str, str]:
    """
    Health check endpoint to verify the API is up and running.
    """
    return {"status": "ok", "message": "Welcome to the vb-intern API!"}
