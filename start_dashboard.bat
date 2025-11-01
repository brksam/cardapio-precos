@echo off
cd /d C:\Users\Administrator\Desktop\juninho
call .\.venv\Scripts\activate.bat
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Administrator\Desktop\serviceAccountKey.json
streamlit run dashboard.py