#!/usr/bin/env bash
# -*- coding: utf-8 -*-

echo "Начало архивации миграций..."

# Определяем директорию скрипта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCHIVE_ROOT="$SCRIPT_DIR/archive"

# Создаём корневую папку архива
mkdir -p "$ARCHIVE_ROOT"

# Для каждого каталога-проекта рядом со скриптом
for project_path in "$SCRIPT_DIR"/*/; do
    project_name="$(basename "$project_path")"
    migrations_dir="$project_path/migrations"

    if [ -d "$migrations_dir" ]; then
        echo
        echo "Проект: $project_name"
        echo "  Найдена папка migrations"

        # Папка-приёмник в архиве
        dest_dir="$ARCHIVE_ROOT/$project_name/migrations"
        mkdir -p "$dest_dir"
        echo "  Копируем в: $dest_dir"

        # Включаем nullglob, чтобы цикл пропускался, если файлов нет
        shopt -s nullglob
        for file in "$migrations_dir"/*.py; do
            filename="$(basename "$file")"
            if [ "$filename" != "__init__.py" ]; then
                echo "    Копирую: $filename"
                cp -v "$file" "$dest_dir/" || echo "    Ошибка при копировании $filename"
            fi
        done
        shopt -u nullglob
    else
        echo
        echo "Проект: $project_name"
        echo "  Папка migrations не найдена"
    fi
done

echo
echo "Архивация завершена."
