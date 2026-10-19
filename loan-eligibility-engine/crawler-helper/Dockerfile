# Playwright base image tag must match the pinned playwright package version (1.48.0)
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
