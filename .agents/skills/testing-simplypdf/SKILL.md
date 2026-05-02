---
name: testing-simplypdf
description: Test the SimplyPDF file hosting platform end-to-end. Use when verifying UI, upload, auth, or payment-related changes.
---

# Testing SimplyPDF (DropSite)

## Environment

- **Live URL:** https://dropsite-production.up.railway.app
- **Custom Domain:** https://simplypdf.io (if DNS is configured)
- **Tech Stack:** FastAPI + SQLite + Jinja2 + vanilla JS
- **Deployment:** Railway (auto-deploys from GitHub on push)

## Prerequisites

- No special secrets needed for basic testing — you can create a test account via `/signup`
- For Stripe checkout testing, the app needs `STRIPE_SECRET_KEY` set in Railway env vars
- Sessions are in-memory, so they reset on each Railway deploy

## Key Test Flows

### 1. User Account Flow
1. Navigate to `/signup`
2. Enter email + password (min 6 chars), click "Create Account"
3. Verify redirect to `/my-sites` with FREE PLAN badge and "0 / 3 sites used"
4. Verify nav shows "My Sites" + "Log Out" (not "Log In" / "Sign Up")

### 2. File Upload with Preview
1. Navigate to homepage `/`
2. Since native file dialog can't be used in automated testing, use JavaScript to set a file:
   ```javascript
   const canvas = document.createElement('canvas');
   canvas.width = 400; canvas.height = 300;
   const ctx = canvas.getContext('2d');
   ctx.fillStyle = '#6495ED'; ctx.fillRect(0, 0, 400, 300);
   ctx.fillStyle = '#FFF'; ctx.font = '24px Arial';
   ctx.fillText('Test Image', 100, 150);
   canvas.toBlob(function(blob) {
     const file = new File([blob], 'test.png', { type: 'image/png' });
     const dt = new DataTransfer(); dt.items.add(file);
     document.getElementById('fileInput').files = dt.files;
     document.getElementById('fileInput').dispatchEvent(new Event('change', { bubbles: true }));
   }, 'image/png');
   ```
3. Verify preview area shows image thumbnail with filename and size
4. Enter a custom slug, click "Upload & Get Link"
5. Verify success message with URL, Analytics Dashboard link, QR Code link

### 3. My Sites Dashboard
1. Navigate to `/my-sites` (must be logged in)
2. Verify uploaded sites appear as cards with slug, filename, view count, expiry date
3. Verify usage counter (e.g., "1 / 3 sites used")
4. Verify Analytics, QR Code, Delete buttons on each card
5. Free tier sites should show expiry date 7 days from upload

### 4. Auth State & Protected Routes
- When logged in: nav shows "My Sites" + "Log Out"
- When logged out: nav shows "Log In" + "Sign Up"
- `/my-sites` redirects to `/login` when not authenticated (302)
- After logout, session cookie is cleared

### 5. Static Pages
- `/terms` — Terms of Service with numbered sections
- `/privacy` — Privacy Policy with data handling details
- `/contact` — Contact form with Name, Email, Message fields
- `/nonexistent-path` — Custom 404 page with "Page not found" and "Go to Homepage" button

### 6. SEO & Branding
- Favicon at `/static/favicon.svg` (purple "S" icon)
- Open Graph meta tags: `og:title`, `og:description`, `twitter:card`
- Footer links to Terms, Privacy, Contact

### 7. Stripe Checkout (Limited Testing)
- Click "Start 7-Day Free Trial" on Pro or Business plan
- Should redirect to Stripe checkout page (requires valid `STRIPE_SECRET_KEY`)
- Cannot complete full payment flow in testing without real card
- Webhook endpoint at `/api/stripe-webhook` processes payment events

## Tips

- File input element has id `fileInput` — use DataTransfer API to set files programmatically
- The upload button is disabled until a file is selected
- Plan limits: Free = 3 sites / 25 MB, Pro = 25 / 100 MB, Business = unlimited / 1 GB
- Rate limiting is 60 requests per minute per IP
- The app uses `simplypdf_session` cookie for auth (httponly, samesite=lax)
- When testing locally, run: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Install deps: `pip install fastapi 'uvicorn[standard]' python-multipart jinja2 aiosqlite 'qrcode[pil]' python-slugify bcrypt stripe`

## Devin Secrets Needed

- None required for basic UI testing (create accounts via /signup)
- `STRIPE_SECRET_KEY` — needed only if testing Stripe checkout flow (set in Railway env vars)
