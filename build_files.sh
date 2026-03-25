#!/bin/bash

echo "=== System Check ==="
python3.12 --version
ls -la

echo "=== Running collectstatic ==="
python3.12 manage.py collectstatic --noinput --verbosity=0

echo "=== Running migrations ==="
python3.12 manage.py migrate --noinput

echo "=== Build Finished ==="
