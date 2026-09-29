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
