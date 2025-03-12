 ```powershell
 [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
 ```
@echo on
echo Начало скрипта
for /d %%a in (*) do (
    echo Найдена папка: %%a
    if exist "%%a\migrations" (
        echo Найдена папка migrations в: %%a
        pushd "%%a\migrations"
        echo Входим в папку migrations
        for %%f in (*.py) do (
            echo Найден файл: %%f
            if not "%%f"=="__init__.py" (
                echo Файл %%f не равен __init__.py
                echo Удаление: %%f
                del "%%f"
                if errorlevel 1 (
                    echo Ошибка при удалении %%f
                )
            )
        )
        popd
        echo Выходим из папки migrations
    ) else (
        echo Папка migrations не найдена в: %%a
    )
)
echo Готово.
pause