FROM python:3.11-slim

WORKDIR /app

COPY . .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" python-multipart jinja2 aiosqlite "qrcode[pil]" python-slugify bcrypt stripe

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
