#!/usr/bin/env bash
# Double-click this file on macOS to launch AI Command Center (Streamlit web UI).
# Opens automatically in the default browser at http://localhost:8501

REPO="$HOME/Projects/ai-command-center"
cd "$REPO"
exec uv run streamlit run app.py --server.address localhost
