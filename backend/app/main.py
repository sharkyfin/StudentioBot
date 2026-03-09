from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import legacy_api, agents
from app.agents.materials_agent import init_materials_table
from app.deps import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_materials_table()
    yield


app = FastAPI(title="Studentio Backend", lifespan=lifespan)

# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(legacy_api.router)
app.include_router(agents.router)

@app.get("/health")
def health():
    return {"ok": True}
