from fastapi import APIRouter, Request, Depends, status
from fastapi.responses import JSONResponse, RedirectResponse
from typing import Any, Dict

from core.errors import AppError
from presentation.container import container, templates
from presentation.middlewares.auth import admin_required, get_current_user

user_router = APIRouter()


@user_router.get("/users", name="users.users_page")
async def users_page(request: Request, user=Depends(admin_required)):
    if isinstance(user, RedirectResponse):
        return user
    return container.render_template(request, "users.html", {"page": "users"})


@user_router.get("/api/users")
async def api_list_users(user=Depends(admin_required)):
    users = container.user_use_cases.list_users()
    return {"ok": True, "users": [u.to_dict() for u in users]}


@user_router.post("/api/users")
async def api_create_user(payload: Dict[str, Any], user=Depends(admin_required)):
    try:
        created = container.user_use_cases.create_user(payload)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content={"ok": True, "user": created.to_dict()})
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@user_router.put("/api/users/{user_id}")
async def api_update_user(user_id: int, payload: Dict[str, Any], user=Depends(admin_required)):
    try:
        updated = container.user_use_cases.update_user(user_id, payload, current_role=user.role if user else "")
        return {"ok": True, "user": updated.to_dict()}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})


@user_router.delete("/api/users/{user_id}")
async def api_delete_user(user_id: int, user=Depends(admin_required)):
    try:
        container.user_use_cases.delete_user(user_id, user.id if user else -1)
        return {"ok": True}
    except AppError as exc:
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.message})
    except Exception as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})
