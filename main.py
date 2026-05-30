#!/usr/bin/env python3
"""F5 Господина — точка входа.

Запуск из корня проекта:
    python3 main.py run     # таблица + команды бота (основной режим)
    python3 main.py once    # один проход опроса — для cron
    python3 main.py init    # засеять состояние без уведомлений
    python3 main.py test    # тестовое сообщение во все чаты подписок
    python3 main.py bot     # только слушатель команд бота
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from polling import main  # noqa: E402 — путь к src добавляется выше

if __name__ == "__main__":
    main()
