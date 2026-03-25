#!/bin/bash

echo "=== Setting up Virtual Environment ==="
python3.12 -m venv venv
source venv/bin/activate

echo "=== Installing dependencies ==="
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Running collectstatic ==="
python3.12 manage.py collectstatic --noinput --verbosity=0

echo "=== Running migrations ==="
python3.12 manage.py migrate --noinput

echo "=== Build Finished ==="
