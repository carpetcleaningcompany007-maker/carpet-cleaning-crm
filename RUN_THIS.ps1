Get-Process python,py -ErrorAction SilentlyContinue | Stop-Process -Force
Set-Location $PSScriptRoot
pip install -r requirements.txt
python app.py
