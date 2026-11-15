# compte_api/routers/sante.py
from fastapi import APIRouter

router = APIRouter()


@router.get("/sante")
def sante() -> dict:
    return {"ok": True}
