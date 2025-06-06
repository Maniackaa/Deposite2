    @echo off
    setlocal enabledelayedexpansion

    :: Корень проекта (где запускается скрипт)
    set "PROJECT_ROOT=%~dp0"
    set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

    :: Папка назначения
    set "DESTINATION=%PROJECT_ROOT%\all_migrations_backup"

    :: Создаём, если не существует
    if not exist "%DESTINATION%" (
        mkdir "%DESTINATION%"
    )

    echo Копирование миграций в: %DESTINATION%
    echo --------------------------------------

    :: Ищем все папки "migrations" с __init__.py
    for /r "%PROJECT_ROOT%" %%d in (.) do (
        if /i "%%~nxd"=="migrations" (
            if exist "%%d\__init__.py" (
                set "SRC=%%d"
                set "REL_PATH=!SRC:%PROJECT_ROOT%=!"
                set "TARGET=%DESTINATION%!REL_PATH!"

                echo Копирование: !REL_PATH!

                mkdir "!TARGET!" >nul 2>&1

                :: Копируем только .py файлы, кроме временных
                for %%f in ("%%d\*.py") do (
                    xcopy "%%f" "!TARGET!\" /Y >nul
                )
            )
        )
    )

    echo --------------------------------------
    echo Готово! Только .py, без мус
