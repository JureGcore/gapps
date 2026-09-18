# Playwright's own images ship the exact Chromium build + OS deps (fonts,
# libX11, etc.) that `playwright install` needs - far more reliable than
# apt-get'ing them by hand.
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The base image's pre-baked browsers don't match what this pip-installed
# playwright build expects at launch (missing chromium_headless_shell) -
# install fresh to guarantee the browsers actually match the driver.
RUN playwright install chromium

COPY seo_checker ./seo_checker
COPY seo_keyword_check.py .

EXPOSE 8080

# CaaS supports exactly one port per container; match it here and in the
# container's Port setting. 2 workers is plenty for a low-traffic internal
# tool and keeps memory use predictable on a small CaaS flavor.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "60", "seo_checker.app:app"]
