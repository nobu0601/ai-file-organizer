@echo off
chcp 65001 >nul
cd /d "%~dp0.."

rem --console: エラーを見たい場合のデバッグ起動（コンソールに出力して終了後に一時停止）
if /i "%~1"=="--console" goto :console

rem 通常起動: コンソールを出さず pythonw で起動
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" src\main.py %*
    exit /b 0
)

:console
if not exist ".venv\Scripts\python.exe" (
    echo [エラー] 仮想環境が見つかりません。先に scripts\setup.bat を実行してください。
    pause
    exit /b 1
)

.venv\Scripts\python.exe src\main.py
echo.
echo 終了しました。
pause
exit /b 0
