#!/bin/bash
# Mac: double-click to open the dashboard in your browser (http://localhost:8501).
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
python3 -m streamlit run dashboard/app.py
