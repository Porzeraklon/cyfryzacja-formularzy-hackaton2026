from fastapi import APIRouter

from backend.app.api.v1 import endpoints_dictionaries, endpoints_forms, endpoints_system

api_router = APIRouter()
api_router.include_router(endpoints_system.router)
api_router.include_router(endpoints_forms.router)
api_router.include_router(endpoints_dictionaries.router)
