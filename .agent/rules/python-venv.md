---
trigger: always_on
---

Rule: Never install Python packages or execute Python scripts in the global environment. You must either activate a local virtual environment (venv) first, or use a native Python dependency manager that handles isolation automatically (e.g., poetry, uv, or pipenv).

Caveat (Shell Context Loss): Do not assume environment state persists across commands. A common error for coding agents is activating a venv in one shell session, but executing the python or pip command in a different, unactivated shell. To prevent this, either chain your commands in a single execution (e.g., .\venv\Scripts\Activate.ps1 && python script.py) or explicitly use the direct executable path (e.g., .\venv\Scripts\python.exe or .\venv\Scripts\pip.exe).