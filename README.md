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
Единый список команд находится в `commands.py`: он используется для `/help` и меню команд
Telegram, которое бот публикует при запуске.

После обновления кода установите зависимости и запустите тесты:

```
venv/bin/pip install -r requirements.txt
venv/bin/pytest
```
