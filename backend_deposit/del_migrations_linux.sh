#!/bin/bash
# Установка кодировки вывода в UTF-8 (это может быть необходимо для корректного отображения не-ASCII символов)
export LANG=ru_RU.UTF-8
export LC_ALL=ru_RU.UTF-8

echo "Начало скрипта"

for dir in *; do
  if [ -d "$dir" ]; then
    echo "Найдена папка: $dir"
    if [ -d "$dir/migrations" ]; then
      echo "Найдена папка migrations в: $dir"
      pushd "$dir/migrations" > /dev/null  # Подавление вывода pushd

      echo "Входим в папку migrations"
      for file in *.py; do
        echo "Найден файл: $file"
        if [ "$file" != "__init__.py" ]; then
          echo "Файл $file не равен __init__.py"
          echo "Удаление: $file"
          rm -f "$file"
          if [ $? -ne 0 ]; then
            echo "Ошибка при удалении $file"
          fi
        fi
      done
      popd > /dev/null # Подавление вывода popd
      echo "Выходим из папки migrations"
    else
      echo "Папка migrations не найдена в: $dir"
    fi
  fi
done

echo "Готово."
read -p "Нажмите Enter для завершения..."  # Пауза эквивалентная pause в Windows