- Бот лежит на сервере амнезии
- Скрипт запуска /etc/systemd/system/basketball.bot.service
- Перезапустить скрипт
```
sudo systemctl restart basketball.bot
```

## Перенос в Proxmox VM 110

Ручное обновление бота в VM `services` (`192.168.1.20`), из root-консоли VM:

```bash
basketball-bot-pull
```

Проверка доступной версии без установки и перезапуска: `basketball-bot-pull --check`.
Команда получает `main` по HTTPS, устанавливает зависимости, запускает тесты и
активирует релиз с проверкой состояния и откатом при ошибке. Таймера обновлений нет.
GitHub Actions выполняет только проверки. Настройка WireGuard/keen-pbr не требуется.
Подготовка VM и первого запуска:
[docs/deploy-proxmox-lxc.md](docs/deploy-proxmox-lxc.md).
Старый systemd unit в корне сохранён; отдельный unit для VM находится в `deploy/`.

## Игры

Игры хранятся в `games.json`; файл не нужно редактировать для обычного управления.
Команды `/addgame` и `/deletegame` доступны только владельцу с Telegram ID из
`OWNER_USER_ID`; `/games` доступна всем. Бот принимает и показывает время по Москве.
Все опросы по расписанию отправляются в 10:00 по Москве. Опросы на понедельник и среду
отправляются накануне. Для разовой игры бот предлагает дату за два дня до матча.
При ручном выборе вводится дата в формате `ДД.ММ.ГГГГ`.
Если предложенное время прошло, первой появляется кнопка «Отправить сейчас».
После заполнения данных опрос отправится сразу при подтверждении предпросмотра.
При запуске бот переносит ещё запланированные разовые опросы на 10:00 выбранной даты.
Если матч начинается в 10:00 или раньше в этот день, бот использует 10:00 предыдущего дня.
В `games.json` даты хранятся в UTC.
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
Время в оповещениях указано по Москве.
Для установки на сервере:

```
sudo apt install -y curl
sudo install -D -m 755 monitor/check-basketball-bot.sh /usr/local/lib/basketball-bot-monitor/check-basketball-bot.sh
sudo install -m 644 monitor/notify.py error_reporting.py /usr/local/lib/basketball-bot-monitor/
sudo install -m 644 monitor/basketball-bot-alert.service /etc/systemd/system/basketball-bot-alert.service
sudo install -m 644 monitor/basketball-bot-healthcheck.service /etc/systemd/system/basketball-bot-healthcheck.service
sudo install -m 644 monitor/basketball-bot-healthcheck.timer /etc/systemd/system/basketball-bot-healthcheck.timer
sudo install -m 644 basketball.bot.service /etc/systemd/system/basketball.bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now basketball-bot-healthcheck.timer
sudo systemctl restart basketball.bot
```

По умолчанию скрипт использует `POLL_BOT_TOKEN` из `/etc/basketball-bot.env` и отправляет
уведомления владельцу из `OWNER_USER_ID` (по умолчанию `41879174`). Для независимого мониторинга можно добавить в этот файл
отдельный `MONITOR_BOT_TOKEN` другого Telegram-бота.

Сообщения бота об ошибках содержат тип и текст исключения. Мониторинг systemd добавляет
состояние процесса, код завершения и последние строки ошибок из журнала. Токены, пароли
и ключи скрываются перед отправкой; полный traceback остаётся на сервере.
Монитор использует системный Python без зависимостей от venv приложения.

Проверка отправляет реальное личное сообщение, но не останавливает сервис:

```
sudo /usr/local/lib/basketball-bot-monitor/check-basketball-bot.sh --test
```
