import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .database import get_db, init_db

# ─── Rate Limiting ────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = now - 60
        self.requests[client_ip] = [t for t in self.requests[client_ip] if t > window]
        if len(self.requests[client_ip]) >= self.requests_per_minute:
            return JSONResponse(
                {"detail": "Too many requests. Please try again later."}, status_code=429
            )
        self.requests[client_ip].append(now)
        return await call_next(request)


# ─── App Setup ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="PDF Trend Finder", version="1.0.0", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

BASE_DIR = Path(__file__).parent
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_COOKIE = "ptf_session"
SESSION_STORE: dict[str, int] = {}

NICHE_CATEGORIES = [
    {"id": "health", "name": "Health & Wellness", "icon": "heart-pulse",
     "queries": ["weight loss tips", "mental health guide", "healthy meal prep",
                 "home workout plan", "sleep better naturally"]},
    {"id": "finance", "name": "Personal Finance", "icon": "wallet",
     "queries": ["budgeting for beginners", "passive income ideas", "how to save money",
                 "investing for beginners", "side hustle ideas"]},
    {"id": "tech", "name": "Technology", "icon": "cpu",
     "queries": ["AI tools for beginners", "learn python programming", "cybersecurity basics",
                 "no-code app builder", "chatgpt prompts"]},
    {"id": "business", "name": "Business & Marketing", "icon": "briefcase",
     "queries": ["social media marketing", "email marketing guide", "start online business",
                 "content marketing strategy", "branding tips"]},
    {"id": "education", "name": "Education & Learning", "icon": "book-open",
     "queries": ["study techniques", "speed reading", "learn a new language",
                 "online course creation", "note taking methods"]},
    {"id": "lifestyle", "name": "Lifestyle & Self-Help", "icon": "sparkles",
     "queries": ["morning routine", "productivity hacks", "minimalism guide",
                 "journaling prompts", "time management"]},
    {"id": "parenting", "name": "Parenting & Family", "icon": "baby",
     "queries": ["toddler activities", "homeschool curriculum", "parenting tips",
                 "family budget planner", "kids meal ideas"]},
    {"id": "creative", "name": "Creative & DIY", "icon": "palette",
     "queries": ["canva design tips", "photography for beginners", "DIY home decor",
                 "digital art tutorial", "craft business ideas"]},
]


def get_current_user_id(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in SESSION_STORE:
        return SESSION_STORE[token]
    return None


# ─── Pages ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(
        request, "index.html", {"user_id": user_id, "categories": NICHE_CATEGORIES}
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    q = request.query_params.get("q", "")
    return templates.TemplateResponse(
        request, "search.html", {"user_id": user_id, "query": q, "categories": NICHE_CATEGORIES}
    )


@app.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM saved_trends WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        saved = [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()

    return templates.TemplateResponse(
        request, "saved.html", {"user_id": user_id, "saved_trends": saved}
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "login", "error": None})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "signup", "error": None})


# ─── Auth API ─────────────────────────────────────────────────────

@app.post("/api/signup")
async def api_signup(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    if not email or not password:
        return templates.TemplateResponse(
            request, "auth.html", {"mode": "signup", "error": "Email and password are required."}
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            request, "auth.html",
            {"mode": "signup", "error": "Password must be at least 6 characters."},
        )

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if await cursor.fetchone():
            return templates.TemplateResponse(
                request, "auth.html",
                {"mode": "signup", "error": "An account with this email already exists."},
            )

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor = await db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash)
        )
        await db.commit()
        user_id = cursor.lastrowid

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user_id
        response = RedirectResponse("/search", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax"
        )
        return response
    finally:
        await db.close()


@app.post("/api/login")
async def api_login(request: Request) -> Response:
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        )
        user = await cursor.fetchone()
        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return templates.TemplateResponse(
                request, "auth.html", {"mode": "login", "error": "Invalid email or password."}
            )

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user["id"]
        response = RedirectResponse("/search", status_code=302)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax"
        )
        return response
    finally:
        await db.close()


@app.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in SESSION_STORE:
        del SESSION_STORE[token]
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ─── Trend API ────────────────────────────────────────────────────

@app.get("/api/trends/search")
async def api_trends_search(q: str = "") -> JSONResponse:
    if not q:
        return JSONResponse({"error": "Provide a query (q)"}, status_code=400)

    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from pytrends.request import TrendReq

    executor = ThreadPoolExecutor(max_workers=2)
    loop = asyncio.get_event_loop()

    results: dict = {"query": q, "related_queries": [], "interest_over_time": []}

    try:
        def _fetch_trends() -> dict:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            data: dict = {
                "related_queries": [],
                "rising_queries": [],
                "interest_over_time": [],
            }

            pytrends.build_payload([q], timeframe="today 3-m")

            try:
                iot = pytrends.interest_over_time()
                if not iot.empty and q in iot.columns:
                    data["interest_over_time"] = [
                        {"date": str(d.date()), "value": int(v)}
                        for d, v in zip(iot.index, iot[q])
                    ]
            except Exception:
                pass

            try:
                rq = pytrends.related_queries()
                if q in rq:
                    top = rq[q].get("top")
                    rising = rq[q].get("rising")
                    if top is not None and not top.empty:
                        data["related_queries"] = [
                            {"query": row["query"], "value": int(row["value"])}
                            for _, row in top.head(15).iterrows()
                        ]
                    if rising is not None and not rising.empty:
                        data["rising_queries"] = [
                            {"query": row["query"], "value": str(row["value"])}
                            for _, row in rising.head(10).iterrows()
                        ]
            except Exception:
                pass

            return data

        trend_data = await loop.run_in_executor(executor, _fetch_trends)
        results.update(trend_data)
    except Exception as e:
        results["error"] = str(e)

    return JSONResponse(results)


@app.get("/api/trends/suggestions")
async def api_trends_suggestions(q: str = "") -> JSONResponse:
    if not q:
        return JSONResponse({"suggestions": []})

    import httpx

    question_prefixes = [
        f"{q}",
        f"how to {q}",
        f"best {q}",
        f"why {q}",
        f"{q} guide",
        f"{q} tips",
        f"{q} for beginners",
        f"what is {q}",
    ]

    all_suggestions: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for prefix in question_prefixes:
            try:
                resp = await client.get(
                    "https://suggestqueries.google.com/complete/search",
                    params={"client": "firefox", "q": prefix},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and len(data) > 1:
                        for s in data[1]:
                            s_lower = s.lower().strip()
                            if s_lower not in seen:
                                seen.add(s_lower)
                                all_suggestions.append(s)
            except Exception:
                continue

    return JSONResponse({"suggestions": all_suggestions[:40]})


@app.get("/api/trends/trending")
async def api_trends_trending() -> JSONResponse:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from pytrends.request import TrendReq

    executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_event_loop()

    try:
        def _fetch_trending() -> list[dict]:
            pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
            df = pytrends.trending_searches(pn="united_states")
            items: list[dict] = []
            for _, row in df.head(20).iterrows():
                items.append({"query": row[0]})
            return items

        trending = await loop.run_in_executor(executor, _fetch_trending)
    except Exception:
        trending = []

    return JSONResponse({"trending": trending})


# ─── Saved Trends API ────────────────────────────────────────────

@app.post("/api/trends/save")
async def api_trends_save(request: Request) -> JSONResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required to save trends")

    body = await request.json()
    query = str(body.get("query", "")).strip()
    category = str(body.get("category", "")).strip()
    notes = str(body.get("notes", "")).strip()

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO saved_trends (user_id, query, category, notes) VALUES (?, ?, ?, ?)",
            (user_id, query, category, notes),
        )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({"status": "saved", "query": query})


@app.delete("/api/trends/save/{trend_id}")
async def api_trends_unsave(request: Request, trend_id: int) -> JSONResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")

    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM saved_trends WHERE id = ? AND user_id = ?", (trend_id, user_id)
        )
        await db.commit()
    finally:
        await db.close()

    return JSONResponse({"status": "deleted"})


# ─── Error Handlers ──────────────────────────────────────────────

@app.exception_handler(404)
async def custom_404(request: Request, exc: HTTPException) -> HTMLResponse:
    return HTMLResponse(
        "<h1>404 - Page Not Found</h1><p><a href='/'>Go home</a></p>", status_code=404
    )


@app.exception_handler(429)
async def custom_429(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": "Too many requests. Please try again later."}, status_code=429
    )
