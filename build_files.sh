#!/bin/bash

echo "=== Installing dependencies ==="
pip install -r requirements.txt

echo "=== Running collectstatic ==="
python3.12 manage.py collectstatic --noinput --verbosity=0

echo "=== Running migrations ==="
python3.12 manage.py migrate --noinput
