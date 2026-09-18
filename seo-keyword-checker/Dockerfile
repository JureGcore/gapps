# Playwright's own images ship the exact Chromium build + OS deps (fonts,
# libX11, etc.) that `playwright install` needs - far more reliable than
# apt-get'ing them by hand.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
RUN echo "=== playwright --version ===" && playwright --version \
    && echo "=== pip show playwright ===" && pip show playwright \
    && echo "=== /ms-playwright contents ===" && find /ms-playwright -maxdepth 2 2>&1

COPY seo_checker ./seo_checker
COPY seo_keyword_check.py .

EXPOSE 8080

# CaaS supports exactly one port per container; match it here and in the
# container's Port setting. 2 workers is plenty for a low-traffic internal
# tool and keeps memory use predictable on a small CaaS flavor.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60", "seo_checker.app:app"]
