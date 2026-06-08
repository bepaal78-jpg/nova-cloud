"""
NOVA Cloud — leichter FastAPI-Server für Render.com (kostenlos, always-on)
Primär-LLM: Groq (gratis, schnell)  |  Fallback: Gemini
Für PC-spezifische Aufgaben: leitet an lokale NOVA-Instanz weiter (wenn erreichbar)
"""

import os, json, time, hashlib
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(title="NOVA Cloud", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────

GROQ_API_KEY  = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PC_URL        = os.getenv("PC_URL", "")        # z.B. http://100.124.172.84:8999 (Tailscale)
NOVA_PASSWORD = os.getenv("NOVA_PASSWORD", "") # optional API-Schutz

SYSTEM_PROMPT = """Du bist NOVA, der persönliche KI-Assistent von Bastian.
Du antwortest auf Deutsch, bist direkt und hilfreich.
Wenn du etwas nicht weißt oder kannst, sagst du es ehrlich.
PC-spezifische Aufgaben (Valorant starten, Dateien öffnen, etc.) funktionieren nur wenn der PC online ist.
Halte deine Antworten kurz und präzise – du wirst hauptsächlich auf dem Handy genutzt."""

# ── In-memory conversation store (pro Session-ID) ─────────────────────────────

conversations: dict[str, list] = {}
MAX_MESSAGES = 20  # pro Session im RAM

def get_session_id(request: Request) -> str:
    """Einfache Session-ID aus IP oder Header"""
    client_id = request.headers.get("X-Session-ID") or \
                request.client.host if request.client else "default"
    return hashlib.md5(client_id.encode()).hexdigest()[:12]

# ── LLM calls ─────────────────────────────────────────────────────────────────

async def call_groq(messages: list) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY nicht gesetzt")
    async with httpx.AsyncClient(timeout=40) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": messages,
                "max_tokens": 1024,
                "temperature": 0.7,
            }
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

async def call_gemini(messages: list) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY nicht gesetzt")
    # Groq-Format → Gemini-Format umwandeln
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    async with httpx.AsyncClient(timeout=40) as client:
        resp = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}",
            json={"contents": contents, "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]}}
        )
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

async def get_ai_response(session_id: str, user_msg: str) -> str:
    # Verlauf aufbauen
    if session_id not in conversations:
        conversations[session_id] = []

    history = conversations[session_id]
    history.append({"role": "user", "content": user_msg})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_MESSAGES:]

    reply = ""
    try:
        reply = await call_groq(messages)
    except Exception as groq_err:
        try:
            reply = await call_gemini(messages)
        except Exception as gem_err:
            reply = f"Beide KI-APIs nicht erreichbar. Groq: {groq_err}. Gemini: {gem_err}"

    history.append({"role": "assistant", "content": reply})
    # Trim
    if len(history) > MAX_MESSAGES * 2:
        conversations[session_id] = history[-MAX_MESSAGES:]

    return reply

# ── PC forwarding ──────────────────────────────────────────────────────────────

PC_KEYWORDS = [
    "valorant", "starte", "öffne programm", "pc", "computer", "desktop",
    "lm studio", "lokale ki", "screen", "bildschirm"
]

def needs_pc(message: str) -> bool:
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in PC_KEYWORDS)

async def try_forward_to_pc(message: str) -> Optional[str]:
    if not PC_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"{PC_URL}/chat",
                json={"message": message},
            )
            if resp.status_code == 200:
                return resp.json().get("reply")
    except Exception:
        pass
    return None

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "NOVA Cloud", "time": int(time.time())}

@app.get("/")
async def root():
    return {"name": "NOVA Cloud", "version": "1.0", "status": "running"}

@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"reply": "Ungültige Anfrage (kein JSON)"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"reply": "Leere Nachricht"}, status_code=400)

    # Optional: Passwort-Schutz
    if NOVA_PASSWORD:
        if body.get("password") != NOVA_PASSWORD:
            return JSONResponse({"reply": "Nicht autorisiert"}, status_code=401)

    session_id = get_session_id(request)

    # PC-Aufgaben → versuche an PC weiterzuleiten
    if needs_pc(message):
        pc_reply = await try_forward_to_pc(message)
        if pc_reply:
            return JSONResponse({"reply": pc_reply, "source": "pc"})
        # PC offline → KI antwortet
        return JSONResponse({
            "reply": "Dein PC ist gerade offline. Ich kann diese Aufgabe (PC-spezifisch) nur ausführen wenn der PC läuft.",
            "source": "cloud",
            "pc_needed": True
        })

    # Normale Anfrage → Cloud KI
    reply = await get_ai_response(session_id, message)
    return JSONResponse({"reply": reply, "source": "cloud"})

@app.delete("/chat/history")
async def clear_history(request: Request):
    session_id = get_session_id(request)
    conversations.pop(session_id, None)
    return {"cleared": True}

@app.get("/pc/status")
async def pc_status():
    """Prüft ob der lokale PC erreichbar ist"""
    if not PC_URL:
        return {"online": False, "reason": "PC_URL nicht konfiguriert"}
    reachable = await try_forward_to_pc("ping") is not None
    # Besser: Health-Check
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(f"{PC_URL}/health")
            online = r.status_code == 200
    except Exception:
        online = False
    return {"online": online, "url": PC_URL}
