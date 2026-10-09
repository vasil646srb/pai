import os
import secrets
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Header, Depends, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from pai.api_schemas import (
    GuestRegisterRequest,
    GuestLoginRequest,
    AuthResponse,
    ChatMessageRequest,
    ChatMessageResponse
)
from pai.personas import PersonaManager, GUEST_PERSONA_ID

app = FastAPI(title=\"PAI - Personal AI Assistant\", version=\"0.1.0\")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[\"*\"],
    allow_credentials=True,
    allow_methods=[\"*\"],
    allow_headers=[\"*\"],
)

ADMIN_DEFAULT_NAME = os.getenv(\"PAI_ADMIN_NAME\", \"admin\")
ADMIN_DEFAULT_CODE = os.getenv(\"PAI_ADMIN_CODE\", \"admin123\")

USERS_DB: Dict[str, Dict[str, Any]] = {}
SESSIONS_DB: Dict[str, Dict[str, Any]] = {}

persona_manager = PersonaManager()

def init_default_admin():
    has_owner = any(u.get(\"role\") == \"owner\" for u in USERS_DB.values())
    if not has_owner:
        USERS_DB[ADMIN_DEFAULT_NAME] = {
            \"code\": ADMIN_DEFAULT_CODE,
            \"role\": \"owner\",
            \"persona_id\": \"default\"
        }
        print(f\"\\n[PAI System] Инициализирован администратор:\")
        print(f\" -> Имя: {ADMIN_DEFAULT_NAME}\")
        print(f\" -> Код: {ADMIN_DEFAULT_CODE}\\n\")

@app.on_event(\"startup\")
async def startup_event():
    init_default_admin()

def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith(\"Bearer \"):
        return {\"name\": \"Owner\", \"role\": \"owner\", \"persona_id\": \"default\"}
    
    token = authorization.split(\" \")[1]
    if token in SESSIONS_DB:
        return SESSIONS_DB[token]
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=\"Недействительный токен авторизации\"
    )

@app.post(\"/api/auth/guest/register\", response_model=AuthResponse)
async def register_guest(req: GuestRegisterRequest):
    if req.name in USERS_DB:
        raise HTTPException(status_code=400, detail=\"Имя уже занято. Используйте вход.\")
    
    USERS_DB[req.name] = {
        \"code\": req.code,
        \"role\": \"guest\",
        \"persona_id\": GUEST_PERSONA_ID
    }
    
    token = secrets.token_hex(16)
    session_data = {
        \"name\": req.name,
        \"role\": \"guest\",
        \"persona_id\": GUEST_PERSONA_ID
    }
    SESSIONS_DB[token] = session_data
    return AuthResponse(
        token=token,
        name=req.name,
        role=\"guest\",
        persona_id=GUEST_PERSONA_ID
    )

@app.post(\"/api/auth/guest/login\", response_model=AuthResponse)
async def login_user(req: GuestLoginRequest):
    user = USERS_DB.get(req.name)
    if not user or user[\"code\"] != req.code:
        raise HTTPException(status_code=401, detail=\"Неверное имя или код доступа.\")
    
    token = secrets.token_hex(16)
    session_data = {
        \"name\": req.name,
        \"role\": user[\"role\"],
        \"persona_id\": user[\"persona_id\"]
    }
    SESSIONS_DB[token] = session_data
    return AuthResponse(
        token=token,
        name=req.name,
        role=user[\"role\"],
        persona_id=user[\"persona_id\"]
    )

@app.get(\"/api/auth/me\")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    return user

@app.post(\"/api/chat\", response_model=ChatMessageResponse)
async def chat_endpoint(
    req: ChatMessageRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    persona_id = GUEST_PERSONA_ID if user[\"role\"] == \"guest\" else (req.persona_id or \"default\")
    persona = persona_manager.get_persona(persona_id)
    
    if not persona:
        raise HTTPException(status_code=404, detail=\"Персона не найдена\")

    reply = f\"[{persona.name}] Здравствуйте, {user['name']}! Вы написали: {req.message}\"
    
    return ChatMessageResponse(
        response=reply,
        session_id=req.session_id or secrets.token_hex(8),
        persona_id=persona_id
    )

@app.get(\"/api/settings\")
async def get_settings(user: Dict[str, Any] = Depends(get_current_user)):
    if user[\"role\"] == \"guest\":
        raise HTTPException(status_code=403, detail=\"Доступ запрещен для гостей.\")
    return {\"status\": \"ok\", \"settings\": {\"model\": \"default\", \"memory_tree\": \"enabled\"}}

def create_app(data_dir: Optional[str] = None, **kwargs) -> FastAPI:
    return app

app.mount(\"/\", StaticFiles(directory=\"pai/static\", html=True), name=\"static\")
with open('pai/app.py', 'w') as f:
    f.write(code)
print('pai/app.py успешно обновлен')

