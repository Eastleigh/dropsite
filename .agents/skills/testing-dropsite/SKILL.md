---
name: testing-dropsite
description: Test the SimplyPDF (DropSite) app locally. Use when verifying UI, checkout, upload, or analytics changes.
---

# Testing SimplyPDF (DropSite)

## Local Dev Setup

```bash
pip install fastapi "uvicorn[standard]" python-multipart jinja2 aiosqlite "qrcode[pil]" python-slugify bcrypt stripe
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The app runs at `http://localhost:8000`. No database setup needed — SQLite is auto-initialized on first run.

## Architecture

- **Backend**: FastAPI (`app/main.py` — single file, ~700 lines)
- **Templates**: Jinja2 in `app/templates/` (index.html, dashboard.html, my_sites.html, auth.html, etc.)
- **Frontend JS**: Inline in templates (no build step, no framework)
- **Database**: SQLite via aiosqlite (auto-created)
- **Deployment**: Railway (auto-deploys from main branch), custom domain `simplypdf.io`

## Key Test Flows

### Upload Flow
1. Go to homepage → drag/drop or click to upload a file
2. The upload POST goes to `/api/upload`
3. Success response includes `{slug, url, dashboard, qr_url}`
4. Result section shows the live URL, copy button, analytics link, QR link

### Checkout / Pricing Flow
1. Scroll to Pricing section on homepage
2. Click "Start 7-Day Free Trial" on Pro or Business cards
3. `handleCheckout()` calls `/api/checkout` with a Stripe price_id
4. Without `STRIPE_SECRET_KEY`, the API returns 503 with an error message
5. With Stripe configured, it returns `{checkout_url}` for redirect

### Auth Flow
- Signup: POST `/api/signup` with email + password
- Login: POST `/api/login`
- Session stored in `SESSION_STORE` (in-memory) with `simplypdf_session` cookie
- `/my-sites` requires auth, redirects to `/login` if not logged in

### Site Serving
- Uploaded sites are served at `/s/{slug}`
- Password-protected sites show a password form first
- Analytics tracked on each visit

## Devin Secrets Needed

- `STRIPE_SECRET_KEY` — needed to test the positive checkout path (redirect to Stripe). Without it, the API returns 503. This is expected for testing error handling.

## Tips

- The default branch might not be `main` — check `git branch -a` to find the correct base branch.
- No pre-commit hooks, no linter configured, no test suite — changes are verified by manual testing.
- The frontend JS is inline in the Jinja2 templates (especially `index.html` which is ~1267 lines). Search for `<script>` tags at the bottom of templates to find JS logic.
- `index.html` contains the upload form, pricing section, and all related JS handlers.
- When testing locally without Stripe, the checkout buttons will trigger the error path — this is useful for testing error handling.
