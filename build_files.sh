#!/bin/bash
set -e

echo "=== Setting up Virtual Environment ==="
python3.12 -m venv venv
source venv/bin/activate

echo "=== Installing dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Creating static directory ==="
mkdir -p staticfiles

echo "=== Running collectstatic ==="
python manage.py collectstatic --noinput --verbosity=0 || echo "Collectstatic skipped"

echo "=== Running migrations ==="
python manage.py migrate --noinput || echo "Migration skipped (some env vars missing during build)"

echo "=== Build Finished ==="
