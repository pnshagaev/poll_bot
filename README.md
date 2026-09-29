- Бот лежит на сервере амнезии
- Скрипт запуска /etc/systemd/system/basketball.bot.service
- Перезапустить скрипт
```
sudo systemctl restart basketball.bot
```

## Игры

Игры хранятся в `games.json`; файл не нужно редактировать для обычного управления.
Команды `/addgame` и `/deletegame` доступны только владельцу с Telegram ID из
`OWNER_USER_ID`; `/games` доступна всем. Все даты, вводимые в `/addgame`, указываются в UTC.
При добавлении игры команды вводятся отдельно: одна из них обязательно `Авито`, а их порядок
в вопросе определяет цвет формы.
Единый список команд находится в `commands.py`: он используется для `/help` и меню команд
Telegram, которое бот публикует при запуске.

Конфигурация production-сервиса находится вне репозитория в `/etc/basketball-bot.env`.
Файл должен содержать `POLL_BOT_TOKEN`, `TG_CHAT_IDS` и `OWNER_USER_ID`, а systemd-сервис
`basketball.bot.service` читает его через `EnvironmentFile`.

После обновления кода установите зависимости и запустите тесты:

```
venv/bin/pip install -r requirements.txt
venv/bin/pytest
```

## Мониторинг systemd

В папке `monitor/` есть service и timer для оповещений в Telegram: при окончательном падении
`basketball.bot` оповещение отправляется сразу, а timer дополнительно проверяет сервис раз в час.
Для установки на сервере:

```
sudo apt install -y curl
sudo install -D -m 755 monitor/check-basketball-bot.sh /usr/local/lib/basketball-bot-monitor/check-basketball-bot.sh
sudo install -m 644 monitor/basketball-bot-alert.service /etc/systemd/system/basketball-bot-alert.service
sudo install -m 644 monitor/basketball-bot-healthcheck.service /etc/systemd/system/basketball-bot-healthcheck.service
sudo install -m 644 monitor/basketball-bot-healthcheck.timer /etc/systemd/system/basketball-bot-healthcheck.timer
sudo install -m 644 basketball.bot.service /etc/systemd/system/basketball.bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now basketball-bot-healthcheck.timer
sudo systemctl restart basketball.bot
```

По умолчанию скрипт использует `POLL_BOT_TOKEN` из `/etc/basketball-bot.env` и отправляет
уведомления Telegram ID `41879174`. Для независимого мониторинга можно добавить в этот файл
отдельный `MONITOR_BOT_TOKEN` другого Telegram-бота.

Проверка отправляет реальное личное сообщение, но не останавливает сервис:

```
sudo /usr/local/lib/basketball-bot-monitor/check-basketball-bot.sh --test
```
