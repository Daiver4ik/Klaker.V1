@echo off
chcp 65001 >nul
title Саундпад Кликер

echo ========================================
echo    Запуск бесплатного KlakerPad!
echo ========================================

REM Переходим в папку, где лежит сам батник (на случай запуска из другого места)
cd /d "%~dp0"

REM Проверяем, есть ли папка с виртуальным окружением .venv в проекте
if exist ".venv\Scripts\python.exe" (
    echo [i] Найдено локальное виртуальное окружение (.venv)
    set "PY_EXE=.venv\Scripts\python.exe"
) else (
    echo [i] Виртуальное окружение не найдено, используем системный Python...
    set "PY_EXE=python"
)

REM Проверяем, установлен ли сам Python вообще
%PY_EXE% --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo [!] ОШИБКА: Python не найден в системе!
    echo Установите Python и убедитесь, что галочка "Add Python to PATH" была включена.
    goto error
)

REM Запускаем ваш скрипт (замените main.py на имя вашего главного файла)
echo [i] Запуск главного файла...
%PY_EXE% main.py

if %errorlevel% neq 0 (
    goto error
)

goto end

:error
echo.
echo ========================================
echo [!] Произошла ошибка во время работы скрипта.
echo ========================================
pause
exit

:end
echo.
echo Сессия завершена.
pause