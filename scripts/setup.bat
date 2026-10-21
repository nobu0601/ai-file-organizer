@echo off
chcp 65001 >nul
cd /d "%~dp0.."

rem --- Python を探す（py -3 を優先、なければ python）---
set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if "%PY%"=="" (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)

if "%PY%"=="" (
    echo [エラー] Python が見つかりません。
    echo python.org から Python 3.10 以上をインストールしてください。
    echo インストーラーで「Add python.exe to PATH」と「tcl/tk and IDLE」にチェックを入れてください。
    pause
    exit /b 1
)

echo === 環境チェック ===
%PY% scripts\check_env.py

rem --- 仮想環境 .venv を作成（無ければ）---
if not exist ".venv\Scripts\python.exe" (
    echo 仮想環境 .venv を作成します。
    %PY% -m venv .venv
    if errorlevel 1 goto :fail_venv
)

echo pip を更新します。
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :fail_pip

echo 依存パッケージをインストールします。
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :fail_pip

echo.
echo ===== セットアップが完了しました =====
echo 起動するには scripts\run.bat を実行してください。
echo.
pause
exit /b 0

:fail_venv
echo [エラー] 仮想環境 .venv の作成に失敗しました。
pause
exit /b 1

:fail_pip
echo [エラー] パッケージのインストールに失敗しました。ネットワーク接続と上のエラーメッセージを確認してください。
pause
exit /b 1
