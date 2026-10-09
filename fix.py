# patch_zip.py
import zipfile
import os

ZIP_IN = "fixed-3.zip"
ZIP_OUT = "fixed-4.zip"

UPDATED_FILES = {
    "pai/personas.py": '''from typing import Dict, Any, Optional
from pydantic import BaseModel

class Persona(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    is_guest_allowed: bool = False

GUEST_PERSONA_ID = "guest_persona"

DEFAULT_PERSONAS: Dict[str, Persona] = {
    "default": Persona(
        id="default",
        name="Основной Ассистент",
        description="Полнофункциональный личный помощник владельца системы.",
        system_prompt="Вы — персональный ИИ-ассистент владельца. У вас есть полный доступ к инструментам и памяти.",
        is_guest_allowed=False
    ),
    GUEST_PERSONA_ID: Persona(
        id=GUEST_PERSONA_ID,
        name="Гостевой Консультант",
        description="Персона для общения с внешними пользователями и гостями в интернет-чате.",
        system_prompt=(
            "Вы — вежливый и полезный гостевой консультант интернет-чата. "
            "Вы общаетесь с гостем. Будьте вежливы, отвечайте на вопросы по существу, "
            "не раскрывайте внутренние конфиденциальные данные владельца и системные настройки."
        ),
        is_guest_allowed=True
    )
}

class PersonaManager:
    def __init__(self):
        self._personas: Dict[str, Persona] = dict(DEFAULT_PERSONAS)

    def get_persona(self, persona_id: str) -> Optional[Persona]:
        return self._personas.get(persona_id)

    def list_personas(self, is_guest: bool = False) -> Dict[str, Persona]:
        if is_guest:
            return {k: v for k, v in self._personas.items() if v.is_guest_allowed}
        return self._personas

    def add_persona(self, persona: Persona):
        self._personas[persona.id] = persona
''',

    "pai/api_schemas.py": '''from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class GuestRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    code: str = Field(..., min_length=4, max_length=32)

class GuestLoginRequest(BaseModel):
    name: str
    code: str

class AuthResponse(BaseModel):
    token: str
    name: str
    role: str
    persona_id: str

class ChatMessageRequest(BaseModel):
    message: str
    persona_id: Optional[str] = None
    session_id: Optional[str] = None

class ChatMessageResponse(BaseModel):
    response: str
    session_id: str
    persona_id: str
''',

    "pai/app.py": '''import secrets
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

app = FastAPI(title="PAI - Personal AI Assistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GUEST_USERS_DB: Dict[str, str] = {}
SESSIONS_DB: Dict[str, Dict[str, Any]] = {}

persona_manager = PersonaManager()

def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        return {"name": "Owner", "role": "owner", "persona_id": "default"}
    
    token = authorization.split(" ")[1]
    if token in SESSIONS_DB:
        return SESSIONS_DB[token]
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Недействительный токен авторизации"
    )

@app.post("/api/auth/guest/register", response_model=AuthResponse)
async def register_guest(req: GuestRegisterRequest):
    if req.name in GUEST_USERS_DB:
        raise HTTPException(status_code=400, detail="Имя уже занято. Используйте вход.")
    
    GUEST_USERS_DB[req.name] = req.code
    token = secrets.token_hex(16)
    session_data = {
        "name": req.name,
        "role": "guest",
        "persona_id": GUEST_PERSONA_ID
    }
    SESSIONS_DB[token] = session_data
    return AuthResponse(
        token=token,
        name=req.name,
        role="guest",
        persona_id=GUEST_PERSONA_ID
    )

@app.post("/api/auth/guest/login", response_model=AuthResponse)
async def login_guest(req: GuestLoginRequest):
    stored_code = GUEST_USERS_DB.get(req.name)
    if not stored_code or stored_code != req.code:
        raise HTTPException(status_code=401, detail="Неверное имя или код доступа.")
    
    token = secrets.token_hex(16)
    session_data = {
        "name": req.name,
        "role": "guest",
        "persona_id": GUEST_PERSONA_ID
    }
    SESSIONS_DB[token] = session_data
    return AuthResponse(
        token=token,
        name=req.name,
        role="guest",
        persona_id=GUEST_PERSONA_ID
    )

@app.get("/api/auth/me")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    return user

@app.post("/api/chat", response_model=ChatMessageResponse)
async def chat_endpoint(
    req: ChatMessageRequest,
    user: Dict[str, Any] = Depends(get_current_user)
):
    persona_id = GUEST_PERSONA_ID if user["role"] == "guest" else (req.persona_id or "default")
    persona = persona_manager.get_persona(persona_id)
    
    if not persona:
        raise HTTPException(status_code=404, detail="Персона не найдена")

    reply = f"[{persona.name}] Здравствуйте, {user['name']}! Вы написали: {req.message}"
    
    return ChatMessageResponse(
        response=reply,
        session_id=req.session_id or secrets.token_hex(8),
        persona_id=persona_id
    )

@app.get("/api/settings")
async def get_settings(user: Dict[str, Any] = Depends(get_current_user)):
    if user["role"] == "guest":
        raise HTTPException(status_code=403, detail="Доступ запрещен для гостей.")
    return {"status": "ok", "settings": {"model": "default", "memory_tree": "enabled"}}

app.mount("/", StaticFiles(directory="pai/static", html=True), name="static")
''',

    "pai/static/index.html": '''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PAI Guest Chat</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div id="auth-modal" class="modal-overlay">
        <div class="modal-card">
            <h2>Вход в Чат</h2>
            <p class="subtitle">Укажите ваше имя и код доступа</p>
            
            <form id="auth-form">
                <div class="form-group">
                    <label for="guest-name">Ваше имя</label>
                    <input type="text" id="guest-name" placeholder="Например: Иван" required minlength="2">
                </div>
                
                <div class="form-group">
                    <label for="guest-code">Код доступа (PIN)</label>
                    <input type="password" id="guest-code" placeholder="****" required minlength="4">
                </div>

                <div id="auth-error" class="error-msg hidden"></div>

                <div class="auth-buttons">
                    <button type="button" id="btn-login" class="btn primary">Войти</button>
                    <button type="button" id="btn-register" class="btn secondary">Зарегистрироваться</button>
                </div>
            </form>
        </div>
    </div>

    <div id="app-container" class="app-container hidden">
        <header class="app-header">
            <div class="user-info">
                <span id="display-user-name">Гость</span>
                <span id="role-badge" class="badge">Guest</span>
            </div>
            <button id="btn-logout" class="btn small">Выйти</button>
        </header>

        <main class="chat-viewport">
            <div id="chat-messages" class="messages-list"></div>
        </main>

        <footer class="chat-input-area">
            <form id="chat-form">
                <input type="text" id="message-input" placeholder="Введите сообщение..." required autocomplete="off">
                <button type="submit" class="btn primary">Отправить</button>
            </form>
        </footer>
    </div>

    <script src="app.js"></script>
</body>
</html>
''',

    "pai/static/app.js": '''const API_BASE = "";

const state = {
    token: null,
    user: null,
    sessionId: null
};

const authModal = document.getElementById("auth-modal");
const appContainer = document.getElementById("app-container");
const guestNameInput = document.getElementById("guest-name");
const guestCodeInput = document.getElementById("guest-code");
const authError = document.getElementById("auth-error");

const btnLogin = document.getElementById("btn-login");
const btnRegister = document.getElementById("btn-register");
const btnLogout = document.getElementById("btn-logout");

const displayUserName = document.getElementById("display-user-name");
const roleBadge = document.getElementById("role-badge");

const chatForm = document.getElementById("chat-form");
const messageInput = document.getElementById("message-input");
const chatMessages = document.getElementById("chat-messages");

document.addEventListener("DOMContentLoaded", async () => {
    const savedName = localStorage.getItem("pai_guest_name");
    const savedCode = localStorage.getItem("pai_guest_code");

    if (savedName && savedCode) {
        guestNameInput.value = savedName;
        guestCodeInput.value = savedCode;
        await handleAuth("/api/auth/guest/login", savedName, savedCode, true);
    } else {
        showModal();
    }
});

function showModal() {
    authModal.classList.remove("hidden");
    appContainer.classList.add("hidden");
}

function hideModal() {
    authModal.classList.add("hidden");
    appContainer.classList.remove("hidden");
}

function showError(msg) {
    authError.textContent = msg;
    authError.classList.remove("hidden");
}

function clearError() {
    authError.textContent = "";
    authError.classList.add("hidden");
}

async function handleAuth(endpoint, name, code, isAuto = false) {
    clearError();
    try {
        const res = await fetch(API_BASE + endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, code })
        });

        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Ошибка авторизации");
        }

        state.token = data.token;
        state.user = data;

        localStorage.setItem("pai_guest_name", name);
        localStorage.setItem("pai_guest_code", code);

        displayUserName.textContent = data.name;
        roleBadge.textContent = data.role;

        hideModal();
        appendSystemMessage(`Добро пожаловать, ${data.name}! Чат готов к работе.`);
    } catch (err) {
        if (isAuto) {
            localStorage.removeItem("pai_guest_name");
            localStorage.removeItem("pai_guest_code");
            showModal();
        } else {
            showError(err.message);
        }
    }
}

btnLogin.addEventListener("click", () => {
    const name = guestNameInput.value.trim();
    const code = guestCodeInput.value.trim();
    if (!name || !code) return showError("Заполните имя и код");
    handleAuth("/api/auth/guest/login", name, code);
});

btnRegister.addEventListener("click", () => {
    const name = guestNameInput.value.trim();
    const code = guestCodeInput.value.trim();
    if (!name || !code) return showError("Заполните имя и код");
    handleAuth("/api/auth/guest/register", name, code);
});

btnLogout.addEventListener("click", () => {
    localStorage.removeItem("pai_guest_name");
    localStorage.removeItem("pai_guest_code");
    state.token = null;
    state.user = null;
    chatMessages.innerHTML = "";
    showModal();
});

chatForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;

    appendUserMessage(text);
    messageInput.value = "";

    try {
        const res = await fetch(API_BASE + "/api/chat", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${state.token}`
            },
            body: JSON.stringify({
                message: text,
                session_id: state.sessionId
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Ошибка отправки");

        state.sessionId = data.session_id;
        appendBotMessage(data.response);
    } catch (err) {
        appendSystemMessage(`Ошибка: ${err.message}`);
    }
});

function appendUserMessage(text) {
    const msg = document.createElement("div");
    msg.className = "message user";
    msg.textContent = text;
    chatMessages.appendChild(msg);
    scrollToBottom();
}

function appendBotMessage(text) {
    const msg = document.createElement("div");
    msg.className = "message bot";
    msg.textContent = text;
    chatMessages.appendChild(msg);
    scrollToBottom();
}

function appendSystemMessage(text) {
    const msg = document.createElement("div");
    msg.className = "message system";
    msg.textContent = text;
    chatMessages.appendChild(msg);
    scrollToBottom();
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
''',

    "pai/static/style.css": '''* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}

body {
    background-color: #f4f6f8;
    color: #333;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
}

.hidden {
    display: none !important;
}

.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 20px;
    z-index: 1000;
}

.modal-card {
    background: #fff;
    padding: 24px;
    border-radius: 12px;
    width: 100%;
    max-width: 360px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
}

.modal-card h2 {
    font-size: 1.4rem;
    margin-bottom: 4px;
}

.modal-card .subtitle {
    font-size: 0.85rem;
    color: #666;
    margin-bottom: 20px;
}

.form-group {
    margin-bottom: 16px;
}

.form-group label {
    display: block;
    font-size: 0.8rem;
    color: #444;
    margin-bottom: 6px;
    font-weight: 600;
}

.form-group input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid #ccc;
    border-radius: 6px;
    font-size: 0.95rem;
}

.error-msg {
    color: #d93025;
    font-size: 0.8rem;
    margin-bottom: 12px;
}

.auth-buttons {
    display: flex;
    gap: 8px;
}

.btn {
    padding: 10px 16px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: 600;
    flex: 1;
}

.btn.primary {
    background-color: #0066ff;
    color: #fff;
}

.btn.secondary {
    background-color: #e5e5ea;
    color: #333;
}

.btn.small {
    padding: 6px 12px;
    font-size: 0.8rem;
    background-color: #f0f0f0;
}

.app-container {
    display: flex;
    flex-direction: column;
    width: 100%;
    max-width: 480px;
    height: 100vh;
    background: #fff;
    box-shadow: 0 0 10px rgba(0,0,0,0.05);
}

.app-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 16px;
    border-bottom: 1px solid #eee;
    background: #fafafa;
}

.user-info {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
}

.badge {
    font-size: 0.7rem;
    padding: 2px 6px;
    background: #0066ff;
    color: #fff;
    border-radius: 10px;
    text-transform: uppercase;
}

.chat-viewport {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
}

.messages-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.message {
    max-width: 80%;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 0.95rem;
    line-height: 1.4;
}

.message.user {
    align-self: flex-end;
    background-color: #0066ff;
    color: #fff;
    border-bottom-right-radius: 2px;
}

.message.bot {
    align-self: flex-start;
    background-color: #f0f0f0;
    color: #222;
    border-bottom-left-radius: 2px;
}

.message.system {
    align-self: center;
    background-color: transparent;
    color: #888;
    font-size: 0.8rem;
}

.chat-input-area {
    padding: 12px 16px;
    border-top: 1px solid #eee;
    background: #fff;
}

#chat-form {
    display: flex;
    gap: 8px;
}

#chat-form input {
    flex: 1;
    padding: 10px 12px;
    border: 1px solid #ccc;
    border-radius: 6px;
    font-size: 0.95rem;
}
'''
}

def main():
    if os.path.exists(ZIP_IN):
        with zipfile.ZipFile(ZIP_IN, 'r') as in_zip, zipfile.ZipFile(ZIP_OUT, 'w', zipfile.ZIP_DEFLATED) as out_zip:
            for item in in_zip.infolist():
                if item.filename not in UPDATED_FILES and not item.filename.endswith(".bak"):
                    out_zip.writestr(item, in_zip.read(item.filename))
            for path, content in UPDATED_FILES.items():
                out_zip.writestr(path, content.strip())
        print(f"Успешно обновлен архив: {ZIP_OUT}")
    else:
        with zipfile.ZipFile(ZIP_OUT, 'w', zipfile.ZIP_DEFLATED) as out_zip:
            for path, content in UPDATED_FILES.items():
                out_zip.writestr(path, content.strip())
        print(f"Создан новый архив: {ZIP_OUT}")

if __name__ == "__main__":
    main()
