# Fortress V2 — AI Daemon для мониторинга

> Event-driven autonomous AI daemon — 24/7 фоновый агент для мониторинга файлов, сети, камер, умного дома

## Быстрый старт

### 1. Запуск

Двойной клик на `start_fortress.bat` на рабочем столе.

Или вручную:
```
cd "C:\Users\badge\Desktop\Проект Вдохновение\Fortress"
python -m fortress
```

### 2. Открой дашборд

Браузер: `http://127.0.0.1:8090`

Дашборд автоматически берёт токен — ничего вводить не нужно.

### 3. Добавь камеру

Нажми кнопку **"+ Add"** в разделе Cameras:
- **Имя**: любое (например "Прихожая")
- **Тип**: RTSP (для IP-камер) или Remote (для удалённых)
- **URL**: адрес потока камеры (см. инструкции ниже)
- Нажми **"Add"**

Камера появится с live-видео прямо в дашборде.

---

## Подключение камер

### Телефон как камера (Android)

1. Установи приложение **"IP Webcam"** из Play Store
2. Открой приложение, нажми **"Start server"**
3. На экране появится адрес: `http://192.168.x.x:8080`
4. В дашборде Fortress: "+ Add" → Тип: RTSP → URL: `rtsp://192.168.x.x:8080`

### Телефон как камера (iPhone)

1. Установи приложение **"TinyCam"** или **"IP Webcam"**
2. Включи RTSP-сервер в настройках приложения
3. Узнай IP телефона: **Настройки → Wi-Fi → нажми на сеть → IP-адрес**
4. В дашборде: "+ Add" → RTSP → URL: `rtsp://192.168.x.x:8080`

### Xiaomi камеры (Mi Home / Yeelight)

**Проблема**: Xiaomi камеры не поддерживают RTSP "из коробки".

**Решение 1 — Прошивка Dafang (рекомендуется)**:

Для моделей: Xiaomi Dafang, Xiaofang, Qingping, GXDL

1. Скачай прошивку: `https://github.com/EliasKotlyar/Xiaomi-Dafang-Hacks`
2. Вставь microSD карту (8-32 ГБ, FAT32)
3. Скопируй файлы прошивки на карту
4. Вставь карту в камеру
5. Камера перезагрузится с новой прошивкой
6. Открой веб-интерфейс: `http://<IP_камеры>`
7. Включи RTSP в настройках
8. URL для Fortress: `rtsp://<IP_камеры>:8554/video1`

**Решение 2 — TinyCam Pro (Android)**:

1. Установи **TinyCam Pro** на Android телефон
2. Добавь Xiaomi камеру в TinyCam (через Mi Cloud аккаунт)
3. Включи RTSP-сервер в TinyCam (настройки → RTSP)
4. URL для Fortress: `rtsp://<IP_телефона>:8554/<имя_камеры>`

**Решение 3 — Родное приложение Mi Home**:

Некоторые новые модели Xiaomi (Aquara G3, etc.) поддерживают RTSP:
1. Открой Mi Home → Настройки камеры → Другие настройки
2. Найди "RTSP" или "RTMP" и включи
3. Установи пароль
4. URL: `rtsp://<IP_камеры>:8554/live`

### RTSP IP-камеры (Hikvision, Dahua, TP-Link, etc.)

1. Узнай IP камеры (в настройках камеры или через утилиту производителя)
2. Узнай порт RTSP (обычно 554 или 8554)
3. URL формат: `rtsp://<user>:<password>@<IP>:<port>/<path>`

Примеры:
```
Hikvision:  rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101
Dahua:      rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0
TP-Link:    rtsp://admin:password@192.168.1.100:554/stream1
Reolink:    rtsp://admin:password@192.168.1.100:554/h264Preview_01_main
```

### USB вебка (локальная)

Подключи вебку к ПК → Fortress автоматически registriрует её как "Local Webcam".

### Удалённая камера (через интернет)

1. В дашборде нажми "+ Add"
2. Выбери тип **"Remote"**
3. Fortress покажет токен для камеры
4. На устройстве (Raspberry Pi, ESP32) запусти скрипт подключения к Fortress по WebSocket

---

## Как узнать IP камеры

### Windows (PowerShell):
```powershell
arp -a | findstr "dynamic"
```
Покажет все устройства в сети. Ищи MAC-адрес камеры.

### Утилита производителя:
- Hikvision: SADP Tool
- Dahua: ConfigTool
- TP-Link: TP-Link IP Scanner

### На самой камере:
Часто IP отображается на экране камеры или в мобильном приложении.

---

## Горячие клавиши OpenCV HUD

Если запущен локальный HUD (отдельное окно):

| Клавиша | Действие |
|---------|----------|
| Q | Выход |
| S | Скриншот |
| P | Пауза/продолжить |
| 1-9 | Выбор камеры |
| H | Вкл/выкл статистику |

---

## Telegram Bot

### Настройка:
1. Создай бота через @BotFather в Telegram
2. Получи токен бота
3. Узнай свой Chat ID (напиши боту /start, потом открой `https://api.telegram.org/bot<TOKEN>/getUpdates`)
4. В `config.yaml`:
```yaml
plugins:
  telegram:
    enabled: true
    bot_token: "твой_токен"
    chat_id: "твой_chat_id"
```

### Команды бота:
```
/status   — статус daemon
/events   — последние события
/cameras  — список камер
/snapshots — последние снапшоты
/rules    — активные правила
/email    — статус email monitor
/test     — тестовое событие
/help     — справка
```

---

## Email Monitor

### Настройка (Gmail):
1. Включи 2-факторную аутентификацию
2. Создай "App Password" в настройках Google
3. В `config.yaml`:
```yaml
plugins:
  email_monitor:
    enabled: true
    imap_server: "imap.gmail.com"
    imap_email: "твой@gmail.com"
    imap_password: "app-password"
    check_interval: 60
```

---

## Конфигурация

Основной файл: `config.yaml`

### Включение плагинов:
```yaml
fortress:
  plugins:
    process_monitor:
      enabled: true        # Мониторинг CPU/RAM
    camera:
      enabled: true        # Камеры
    file_watcher:
      enabled: false       # Мониторинг файлов
    network_monitor:
      enabled: false       # Мониторинг сети
    mqtt:
      enabled: false       # MQTT (умный дом)
    home_assistant:
      enabled: false       # Home Assistant
    telegram:
      enabled: false       # Telegram бот
    email_monitor:
      enabled: false       # Email
```

### Безопасность:
```yaml
security:
  destructive_approval: true  # Требовать подтверждение для опасных действий
  allowed_paths:
    - "~/Desktop"
    - "~/Documents"
```

---

## Устранение проблем

### Камера не показывает видео
1. Проверь IP и порт камеры
2. Открой RTSP-адрес в браузере или VLC
3. Проверь Firewall (разреши Python)
4. Убедись что телефон/камера и ПК в одной WiFi сети

### Порт 8090 занят
Закрой предыдущий Fortress (Task Manager → python.exe → End task) или перезапусти `start_fortress.bat` (он убивает старые процессы).

### "Config not found"
Убедись что `config.yaml` в папке Fortress. Bat файл уже настроен на правильный путь.

### Ollama не найден
 Fortress работает без Ollama (только rules engine). Для AI-анализа камер нужно установить Ollama + модели:
```
ollama pull gemma2:2b
ollama pull qwen2.5:14b
ollama pull llava
```

---

## Тесты

```bash
cd "C:\Users\badge\Desktop\Проект Вдохновение\Fortress"
python -m pytest tests/ -v
```

115/115 тестов должны пройти.

---

## Структура проекта

```
Fortress/
├── fortress/
│   ├── core/          — ядро (auth, config, event bus, rules, actions, DB)
│   ├── hud/           — OpenCV HUD overlay
│   ├── plugins/       — плагины (camera, telegram, email, mqtt, etc.)
│   ├── web/           — дашборд + API
│   ├── reasoner/      — LLM reasoner (two-tier)
│   └── main.py        — точка входа
├── tests/             — 115 тестов
├── config.yaml        — конфигурация
└── start_fortress.bat — запуск
```
