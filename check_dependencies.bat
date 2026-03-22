@echo off
:: SparkyBot Dependency Checker
python -c "import watchdog; print('watchdog OK')" 2>nul || echo MISSING: watchdog
python -c "import requests; print('requests OK')" 2>nul || echo MISSING: requests
python --version
echo.
echo Install missing with: pip install -r requirements.txt
pause
