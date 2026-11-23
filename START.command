#!/bin/bash
set -e
cd "$(dirname "$0")"
python3 -m venv .setup-venv
.setup-venv/bin/python -m pip install --quiet pynacl certifi
exec .setup-venv/bin/python setup_app.py
