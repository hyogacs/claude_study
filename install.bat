@echo off
chcp 65001 >nul
echo ========================================
echo   投資管理システム - 環境セットアップ
echo ========================================
echo.

:: Python確認
python --version >nul 2>&1
if errorlevel 1 (
    echo [エラー] Pythonが見つかりません。
    echo Python 3.10以上をインストールしてください。
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] 仮想環境を作成中...
if not exist "venv" (
    python -m venv venv
    echo       仮想環境を作成しました。
) else (
    echo       仮想環境は既に存在します。スキップします。
)

echo.
echo [2/3] 仮想環境を有効化中...
call venv\Scripts\activate.bat

echo.
echo [3/3] 依存パッケージをインストール中...
pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt

echo.
echo ========================================
echo   セットアップ完了！
echo   start.bat でアプリを起動できます。
echo ========================================
pause
