@echo off
chcp 65001 >nul
echo ========================================
echo   投資管理システム - 起動中
echo ========================================
echo.

:: 仮想環境確認
if not exist "venv\Scripts\activate.bat" (
    echo [エラー] 仮想環境が見つかりません。
    echo 先に install.bat を実行してください。
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo アプリケーションを起動します...
echo http://localhost:5000 でアクセスできます。
echo 終了するには Ctrl+C を押してください。
echo.

python app.py
