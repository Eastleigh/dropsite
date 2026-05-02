import mimetypes
import os
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .database import get_db, init_db, migrate_db
from .utils import (
    MAX_UPLOAD_SIZE,
    UPLOAD_DIR,
    cleanup_site,
    extract_upload,
    generate_qr_code,
    generate_slug,
    hash_password,
    verify_password,
)

# Plan limits
PLAN_LIMITS = {
    "free": {"max_sites": 3, "max_upload_bytes": 25 * 1024 * 1024, "max_upload_label": "25 MB"},
    "pro": {"max_sites": 25, "max_upload_bytes": 100 * 1024 * 1024, "max_upload_label": "100 MB"},
    "business": {"max_sites": 999999, "max_upload_bytes": 1024 * 1024 * 1024, "max_upload_label": "1 GB"},
}
FREE_EXPIRY_DAYS = 7


# ─── Rate Limiting Middleware ─────────────────────────────────────

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
                {"detail": "Too many requests. Please try again later."},
                status_code=429,
            )

        self.requests[client_ip].append(now)
        return await call_next(request)


# ─── App Setup ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    await migrate_db()
    yield


app = FastAPI(title="SimplyPDF", version="2.0.0", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60)

BASE_DIR = Path(__file__).parent
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SESSION_COOKIE = "simplypdf_session"
SESSION_STORE: dict[str, int] = {}


def get_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def get_current_user_id(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token and token in SESSION_STORE:
        return SESSION_STORE[token]
    return None


async def get_user_plan(user_id: int | None) -> str:
    if not user_id:
        return "free"
    db = await get_db()
    try:
        cursor = await db.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        return user["plan"] if user else "free"
    finally:
        await db.close()


# ─── Pages ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(request, "index.html", {"user_id": user_id})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "login", "error": None})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"mode": "signup", "error": None})


@app.get("/my-sites", response_class=HTMLResponse)
async def my_sites_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    if not user_id:
        return RedirectResponse("/login", status_code=302)

    db = await get_db()
    try:
        cursor = await db.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        plan = user["plan"] if user else "free"

        cursor = await db.execute(
            "SELECT * FROM sites WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,),
        )
        sites = [dict(r) for r in await cursor.fetchall()]

        for site in sites:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM analytics WHERE site_id = ?", (site["id"],)
            )
            row = await cursor.fetchone()
            site["views"] = row["cnt"] if row else 0

        limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        return templates.TemplateResponse(
            request,
            "my_sites.html",
            {"sites": sites, "plan": plan, "limits": limits, "user_id": user_id},
        )
    finally:
        await db.close()


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(request, "legal.html", {"page": "terms", "user_id": user_id})


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(request, "legal.html", {"page": "privacy", "user_id": user_id})


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request) -> HTMLResponse:
    user_id = get_current_user_id(request)
    return templates.TemplateResponse(request, "contact.html", {"user_id": user_id, "sent": False})


@app.get("/dashboard/{slug}", response_class=HTMLResponse)
async def dashboard(request: Request, slug: str) -> HTMLResponse:
    user_id = get_current_user_id(request)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE slug = ?", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM analytics WHERE site_id = ?", (site["id"],)
        )
        row = await cursor.fetchone()
        total_views = row["cnt"] if row else 0

        cursor = await db.execute(
            """SELECT DATE(visited_at) as day, COUNT(*) as cnt
               FROM analytics WHERE site_id = ?
               GROUP BY DATE(visited_at) ORDER BY day DESC LIMIT 30""",
            (site["id"],),
        )
        daily_views = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT referer, COUNT(*) as cnt
               FROM analytics WHERE site_id = ? AND referer IS NOT NULL AND referer != ''
               GROUP BY referer ORDER BY cnt DESC LIMIT 10""",
            (site["id"],),
        )
        top_referers = [dict(r) for r in await cursor.fetchall()]

        base_url = get_base_url(request)
        site_url = f"{base_url}/s/{slug}"

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "site": dict(site),
                "site_url": site_url,
                "total_views": total_views,
                "daily_views": daily_views,
                "top_referers": top_referers,
                "user_id": user_id,
            },
        )
    finally:
        await db.close()


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
            request, "auth.html", {"mode": "signup", "error": "Password must be at least 6 characters."}
        )

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM users WHERE email = ?", (email,))
        if await cursor.fetchone():
            return templates.TemplateResponse(
                request, "auth.html", {"mode": "signup", "error": "An account with this email already exists."}
            )

        pw_hash = hash_password(password)
        cursor = await db.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, pw_hash),
        )
        await db.commit()
        user_id = cursor.lastrowid

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user_id
        response = RedirectResponse("/my-sites", status_code=302)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")
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
        cursor = await db.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,))
        user = await cursor.fetchone()
        if not user or not verify_password(password, user["password_hash"]):
            return templates.TemplateResponse(
                request, "auth.html", {"mode": "login", "error": "Invalid email or password."}
            )

        token = secrets.token_urlsafe(32)
        SESSION_STORE[token] = user["id"]
        response = RedirectResponse("/my-sites", status_code=302)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=30 * 24 * 3600, samesite="lax")
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


# ─── Upload API ───────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_site(
    request: Request,
    file: UploadFile = File(...),
    custom_slug: str = Form(""),
    password: str = Form(""),
) -> JSONResponse:
    user_id = get_current_user_id(request)
    plan = await get_user_plan(user_id)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])

    file_bytes = await file.read()
    if len(file_bytes) > limits["max_upload_bytes"]:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {limits['max_upload_label']} on {plan} plan)",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Check site count limit
    if user_id:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM sites WHERE user_id = ? AND is_active = 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row and row["cnt"] >= limits["max_sites"]:
                raise HTTPException(
                    status_code=403,
                    detail=f"You've reached the {limits['max_sites']} site limit on the {plan} plan. Upgrade to get more.",
                )
        finally:
            await db.close()

    slug = custom_slug.strip().lower().replace(" ", "-") if custom_slug.strip() else generate_slug()

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM sites WHERE slug = ?", (slug,))
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="That link name is already taken")

        site_dir = extract_upload(file_bytes, file.filename, slug)
        password_hash = hash_password(password) if password else None

        expires_at = None
        if plan == "free":
            expires_at = (datetime.now(timezone.utc) + timedelta(days=FREE_EXPIRY_DAYS)).isoformat()

        await db.execute(
            """INSERT INTO sites (slug, user_id, original_filename, site_dir, password_hash, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slug, user_id, file.filename, str(site_dir), password_hash, expires_at),
        )
        await db.commit()
    finally:
        await db.close()

    base_url = get_base_url(request)
    return JSONResponse(
        {
            "slug": slug,
            "url": f"{base_url}/s/{slug}",
            "dashboard": f"{base_url}/dashboard/{slug}",
            "qr_url": f"{base_url}/api/qr/{slug}",
        }
    )


# ─── Stripe Checkout ─────────────────────────────────────────────

@app.post("/api/checkout")
async def create_checkout(request: Request) -> JSONResponse:
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured yet. Contact us for early access.",
        )

    try:
        import stripe

        stripe.api_key = stripe_key
        body = await request.json()
        price_id = body.get("price_id")
        if not price_id:
            raise HTTPException(status_code=400, detail="Missing price_id")

        base_url = get_base_url(request)

        checkout_params: dict = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": f"{base_url}/signup?checkout=success",
            "cancel_url": f"{base_url}/?checkout=cancel",
        }

        user_id = get_current_user_id(request)
        if user_id:
            db = await get_db()
            try:
                cursor = await db.execute("SELECT email, stripe_customer_id FROM users WHERE id = ?", (user_id,))
                user = await cursor.fetchone()
                if user and user["stripe_customer_id"]:
                    checkout_params["customer"] = user["stripe_customer_id"]
                elif user:
                    checkout_params["customer_email"] = user["email"]
            finally:
                await db.close()

        session = stripe.checkout.Session.create(**checkout_params)
        return JSONResponse({"checkout_url": session.url})
    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe SDK not installed")


# ─── Stripe Webhook ──────────────────────────────────────────────

@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    try:
        import stripe
        stripe.api_key = stripe_key

        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        if webhook_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        else:
            import json
            event = stripe.Event.construct_from(json.loads(payload), stripe.api_key)

        event_type = event["type"]

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_details", {}).get("email", "")
            customer_id = session.get("customer")
            subscription_id = session.get("subscription")

            if customer_email:
                db = await get_db()
                try:
                    cursor = await db.execute("SELECT id FROM users WHERE email = ?", (customer_email.lower(),))
                    user = await cursor.fetchone()
                    if user:
                        await db.execute(
                            "UPDATE users SET plan = 'pro', stripe_customer_id = ?, stripe_subscription_id = ? WHERE id = ?",
                            (customer_id, subscription_id, user["id"]),
                        )
                        await db.commit()
                finally:
                    await db.close()

        elif event_type in ("customer.subscription.deleted", "customer.subscription.updated"):
            subscription = event["data"]["object"]
            customer_id = subscription.get("customer")
            status = subscription.get("status")

            if customer_id:
                db = await get_db()
                try:
                    if status in ("canceled", "unpaid", "past_due"):
                        await db.execute(
                            "UPDATE users SET plan = 'free' WHERE stripe_customer_id = ?",
                            (customer_id,),
                        )
                    elif status == "active":
                        await db.execute(
                            "UPDATE users SET plan = 'pro' WHERE stripe_customer_id = ?",
                            (customer_id,),
                        )
                    await db.commit()
                finally:
                    await db.close()

        return JSONResponse({"status": "ok"})

    except Exception:
        raise HTTPException(status_code=400, detail="Webhook error")


# ─── Other API ────────────────────────────────────────────────────

@app.get("/api/qr/{slug}")
async def get_qr(request: Request, slug: str) -> Response:
    base_url = get_base_url(request)
    site_url = f"{base_url}/s/{slug}"
    qr_bytes = generate_qr_code(site_url)
    return Response(content=qr_bytes, media_type="image/png")


@app.get("/api/stats/{slug}")
async def get_stats(slug: str) -> JSONResponse:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE slug = ?", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM analytics WHERE site_id = ?", (site["id"],)
        )
        row = await cursor.fetchone()
        total = row["cnt"] if row else 0

        cursor = await db.execute(
            """SELECT DATE(visited_at) as day, COUNT(*) as cnt
               FROM analytics WHERE site_id = ?
               GROUP BY DATE(visited_at) ORDER BY day DESC LIMIT 30""",
            (site["id"],),
        )
        daily = [dict(r) for r in await cursor.fetchall()]

        return JSONResponse({"total_views": total, "daily_views": daily})
    finally:
        await db.close()


@app.delete("/api/site/{slug}")
async def delete_site(request: Request, slug: str) -> JSONResponse:
    user_id = get_current_user_id(request)
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, user_id FROM sites WHERE slug = ?", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        if site["user_id"] and site["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="You don't have permission to delete this site")

        await db.execute("DELETE FROM analytics WHERE site_id = ?", (site["id"],))
        await db.execute("DELETE FROM sites WHERE id = ?", (site["id"],))
        await db.commit()
        cleanup_site(slug)
        return JSONResponse({"message": "Site deleted"})
    finally:
        await db.close()


@app.post("/api/contact")
async def api_contact(request: Request) -> HTMLResponse:
    form = await request.form()
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    message = str(form.get("message", "")).strip()

    if not name or not email or not message:
        return templates.TemplateResponse(
            request, "contact.html",
            {"user_id": get_current_user_id(request), "sent": False, "error": "All fields are required."},
        )

    # In production, send email via SMTP or a service like SendGrid
    # For now, log it (visible in Railway logs)
    print(f"[CONTACT] From: {name} <{email}> — {message}")

    user_id = get_current_user_id(request)
    return templates.TemplateResponse(
        request, "contact.html", {"user_id": user_id, "sent": True}
    )


# ─── Serve hosted sites ──────────────────────────────────────────

@app.post("/s/{slug}/unlock")
async def unlock_site(request: Request, slug: str) -> HTMLResponse:
    form = await request.form()
    password = str(form.get("password", ""))

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE slug = ? AND is_active = 1", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        if not site["password_hash"] or not verify_password(password, site["password_hash"]):
            return templates.TemplateResponse(
                request,
                "password.html",
                {"slug": slug, "error": "Incorrect password"},
            )

        await db.execute(
            "INSERT INTO analytics (site_id, ip_address, user_agent, referer) VALUES (?, ?, ?, ?)",
            (
                site["id"],
                request.client.host if request.client else None,
                request.headers.get("user-agent"),
                request.headers.get("referer"),
            ),
        )
        await db.commit()
    finally:
        await db.close()

    return await _serve_site_files(slug, "index.html")


@app.get("/s/{slug}")
@app.get("/s/{slug}/{path:path}")
async def serve_site(request: Request, slug: str, path: str = "") -> Response:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM sites WHERE slug = ? AND is_active = 1", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        if site["password_hash"]:
            return templates.TemplateResponse(
                request,
                "password.html",
                {"slug": slug, "error": None},
            )

        if not path:
            await db.execute(
                "INSERT INTO analytics (site_id, ip_address, user_agent, referer) VALUES (?, ?, ?, ?)",
                (
                    site["id"],
                    request.client.host if request.client else None,
                    request.headers.get("user-agent"),
                    request.headers.get("referer"),
                ),
            )
            await db.commit()
    finally:
        await db.close()

    return await _serve_site_files(slug, path)


async def _serve_site_files(slug: str, path: str) -> Response:
    site_dir = UPLOAD_DIR / slug
    if not site_dir.exists():
        raise HTTPException(status_code=404, detail="Site files not found")

    if not path:
        index = site_dir / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")
        files = list(site_dir.iterdir())
        if len(files) == 1 and files[0].is_file():
            mime = mimetypes.guess_type(files[0].name)[0] or "application/octet-stream"
            return FileResponse(str(files[0]), media_type=mime)
        from html import escape
        file_list = [f.name for f in files if f.is_file()]
        html = "<html><body><h2>Files</h2><ul>"
        for f in file_list:
            html += f'<li><a href="/s/{escape(slug)}/{escape(f)}">{escape(f)}</a></li>'
        html += "</ul></body></html>"
        return HTMLResponse(html)

    file_path = (site_dir / path).resolve()
    if not str(file_path).startswith(str(site_dir.resolve()) + os.sep):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(str(file_path), media_type=mime)


# ─── Custom 404 ──────────────────────────────────────────────────

@app.exception_handler(404)
async def custom_404(request: Request, exc: HTTPException) -> HTMLResponse:
    return templates.TemplateResponse(request, "404.html", status_code=404)


@app.exception_handler(429)
async def custom_429(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": "Too many requests. Please try again later."},
        status_code=429,
    )
