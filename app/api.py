"""FastAPI application assembly. Run with:

    uvicorn app.api:app --port 8765
"""

# Third Party
from fastapi import FastAPI

# Local
from .routes import router

app = FastAPI(title="Import Tracker")
app.include_router(router)
