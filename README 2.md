# Morning Star Bot

## Запуск
1. Создайте бота в @BotFather, скопируйте токен.
2. Узнайте свой Telegram ID через @userinfobot.
3. Скопируйте `.env.example` в `.env` и заполните `BOT_TOKEN` и `OWNER_ID`.
4. `pip install -r requirements.txt`
5. `python bot.py`

Владелец открывает админ-панель командой `/admin` и добавляет остальных администраторов по Telegram ID.

## Работа 24/7
Запустите на VPS (от ~200 ₽/мес) или на хостинге ботов (Railway, Render, Bothost и т.п.).
Пример systemd-сервиса `/etc/systemd/system/msbot.service`:

    [Unit]
    Description=Morning Star Bot
    After=network.target
    [Service]
    WorkingDirectory=/opt/msbot
    ExecStart=/usr/bin/python3 bot.py
    Restart=always
    [Install]
    WantedBy=multi-user.target

Затем: `systemctl enable --now msbot`. Данные хранятся в `morning_star.db` — делайте её резервные копии.

## Настройка
Баннер главного меню — файл `banner.jpg` рядом с `bot.py` (замените на свой, 1280×720).
В начале `bot.py`: список должностей `POSITIONS`, вопросы анкеты `QUESTIONS`, категории жалоб, лимиты.
Новые языки: добавьте словарь в `TEXTS`.

## Docker (рекомендуется)
На сервере с установленным Docker:

    cp .env.example .env      # впишите BOT_TOKEN и OWNER_ID
    docker compose up -d --build

- Логи: `docker compose logs -f`
- Остановить: `docker compose down`
- Обновить после правки bot.py: `docker compose up -d --build`
- База данных хранится в папке `./data` рядом с проектом — копируйте её для резервной копии.
