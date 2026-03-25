#!/bin/bash

echo "=== Installing dependencies ==="
python3.12 -m pip install -r requirements.txt --break-system-packages

echo "=== Running collectstatic ==="
python3.12 manage.py collectstatic --noinput --verbosity=0

echo "=== Running migrations ==="
python3.12 manage.py migrate --noinput

echo "=== Build Finished ==="
