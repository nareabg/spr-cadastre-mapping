@echo off
REM Opens the dashboard in your browser (http://localhost:8501).
REM Run the pipeline first (python run_all.py) so output\ has the results.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m streamlit run dashboard\app.py
pause
