# DropSite

**The fastest way to host & share files online — no account needed.**

A better alternative to [tiiny.host](https://tiiny.host). Upload any file — HTML sites, PDFs, images, documents — and get a shareable link in seconds. No sign-up required.

## Features

- **Instant Deploy** — Drag & drop, get a link. No account needed.
- **Custom Link Names** — Choose your own URL slug (`/s/my-portfolio`).
- **Password Protection** — Restrict access with a password.
- **Built-in Analytics** — Track views, referrers, daily traffic.
- **Auto QR Codes** — Every hosted file gets a downloadable QR code.
- **100 MB Upload Limit** — Generous free tier.
- **Open Source** — MIT licensed, self-hostable.

## Quick Start

```bash
# Install dependencies
pip install -e .

# Run the server
uvicorn app.main:app --reload

# Open http://localhost:8000
```

## Docker

```bash
docker build -t dropsite .
docker run -p 8000:8000 dropsite
```

## API

### Upload a file
```bash
curl -X POST http://localhost:8000/api/upload \
  -F "file=@mysite.zip" \
  -F "custom_slug=my-portfolio" \
  -F "password=secret123"
```

### Get analytics
```bash
curl http://localhost:8000/api/stats/my-portfolio
```

### Get QR code
```bash
curl http://localhost:8000/api/qr/my-portfolio -o qr.png
```

### Delete a site
```bash
curl -X DELETE http://localhost:8000/api/site/my-portfolio
```

## Tech Stack

- **Backend**: FastAPI + SQLite + aiosqlite
- **Frontend**: Vanilla HTML/CSS/JS (no framework bloat)
- **QR Codes**: qrcode library with Pillow
- **Password Hashing**: bcrypt

## License

MIT
