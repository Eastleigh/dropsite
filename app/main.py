import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_db, init_db
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield


app = FastAPI(title="DropSite", version="1.0.0", lifespan=lifespan)

BASE_DIR = Path(__file__).parent
_static_dir = BASE_DIR / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# ─── Pages ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/dashboard/{slug}", response_class=HTMLResponse)
async def dashboard(request: Request, slug: str) -> HTMLResponse:
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
            },
        )
    finally:
        await db.close()


# ─── API ──────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_site(
    request: Request,
    file: UploadFile = File(...),
    custom_slug: str = Form(""),
    password: str = Form(""),
) -> JSONResponse:
    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 100 MB)")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    slug = custom_slug.strip().lower().replace(" ", "-") if custom_slug.strip() else generate_slug()

    # Check slug availability
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM sites WHERE slug = ?", (slug,))
        if await cursor.fetchone():
            raise HTTPException(status_code=409, detail="That link name is already taken")

        site_dir = extract_upload(file_bytes, file.filename, slug)
        password_hash = hash_password(password) if password else None

        await db.execute(
            """INSERT INTO sites (slug, original_filename, site_dir, password_hash)
               VALUES (?, ?, ?, ?)""",
            (slug, file.filename, str(site_dir), password_hash),
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
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{base_url}/?checkout=success",
            cancel_url=f"{base_url}/?checkout=cancel",
        )
        return JSONResponse({"checkout_url": session.url})
    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe SDK not installed")


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
async def delete_site(slug: str) -> JSONResponse:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM sites WHERE slug = ?", (slug,))
        site = await cursor.fetchone()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

        await db.execute("DELETE FROM analytics WHERE site_id = ?", (site["id"],))
        await db.execute("DELETE FROM sites WHERE id = ?", (site["id"],))
        await db.commit()
        cleanup_site(slug)
        return JSONResponse({"message": "Site deleted"})
    finally:
        await db.close()


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

        # Record visit
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

    # Serve the site
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

        # Record visit (only for main page, not assets)
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
        # Try index.html first, then any single file
        index = site_dir / "index.html"
        if index.exists():
            return FileResponse(str(index), media_type="text/html")
        # If only one file, serve it
        files = list(site_dir.iterdir())
        if len(files) == 1 and files[0].is_file():
            mime = mimetypes.guess_type(files[0].name)[0] or "application/octet-stream"
            return FileResponse(str(files[0]), media_type=mime)
        # Show directory listing
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
