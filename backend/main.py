from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import urls
from services.service import executor


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    executor.shutdown(wait=True)


app = FastAPI(
    title="Freistellung Service",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(urls.router, tags=["Image Management"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
