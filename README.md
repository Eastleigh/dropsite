# PDF Trend Finder

**Find trending PDF guide ideas using real-time search data — 100% free.**

A better alternative to [PDF Trend Lab](https://pdftrendlabapp.com/). Discover what problems people are searching for right now and create PDF guides that actually sell.

## Features

- **Real-Time Trend Search** — Powered by Google Trends, see what's trending in any niche.
- **40+ Idea Suggestions** — Question-based search suggestions for any topic.
- **Interest Over Time Charts** — Visualize trend data over the last 3 months.
- **Related & Rising Queries** — Discover adjacent topics and breakout searches.
- **Save & Organize Ideas** — Bookmark your best ideas with a free account.
- **8 Niche Categories** — Health, Finance, Tech, Business, Education, Lifestyle, Parenting, Creative.
- **100% Free** — No paywall, no hidden fees.

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
docker build -t pdf-trend-finder .
docker run -p 8000:8000 pdf-trend-finder
```

## Tech Stack

- **Backend**: FastAPI + SQLite + aiosqlite
- **Trend Data**: pytrends (Google Trends) + Google Autocomplete API
- **Frontend**: Vanilla HTML/CSS/JS + Chart.js
- **Auth**: bcrypt password hashing + session cookies

## License

MIT
