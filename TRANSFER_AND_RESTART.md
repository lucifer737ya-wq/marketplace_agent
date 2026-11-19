# Перенос файлов на ПК и перезапуск тихого режима

Ветка этого обновления: **`arena/0c7b2c3d-marketplace-agent`**
(база — `main`, коммит `73a7dfc`). Все команды ниже вводятся в **cmd**
(`Win+R` → `cmd` → Enter). Копируйте блоками, по одному.

> Ваши рабочие файлы **не перезаписываются**: `.env`, `logs\`, `backups\`,
> `database\store.db` и профили браузера в git и в архиве отсутствуют.
> Исключение — если вы сами перезапишете `.env` вручную: в архиве лежит
> только `.env.example`.

---

## 0. Что нового появилось в этой поставке

| Файл | Назначение |
|---|---|
| `doctor.py` + `doctor.bat` | офлайн-диагностика: Python, зависимости, `.env`, права, SQLite, маршруты, Chromium |
| `setup_env.bat` | создаёт `venv`, ставит зависимости и Chromium, запускает диагностику |
| `stop_agent.bat` | останавливает **только** процессы этого проекта (по пути к `supervisor.py`/`worker.py`) |
| `runtime_bootstrap.py` | пишет traceback в `logs\*.log` при скрытом запуске через `pythonw.exe` |
| `start_hidden.vbs` | проверяет наличие `venv` и `supervisor.py`, при отсутствии показывает окно с подсказкой |

---

## 1. Вариант A. Git на ПК уже настроен (предпочтительно)

### 1.1 Чистая копия в новую папку

```cmd
cd /d C:\
git clone https://github.com/lucifer737ya-wq/marketplace_agent.git marketplace_agent_new
cd /d C:\marketplace_agent_new
git checkout arena/0c7b2c3d-marketplace-agent
copy /Y C:\marketplace_agent\.env C:\marketplace_agent_new\.env
```

Если старой папки нет — просто создайте `.env` из шаблона и заполните ключи:

```cmd
cd /d C:\marketplace_agent_new
copy /Y .env.example .env
notepad .env
```

### 1.2 Обновление существующей папки `C:\marketplace_agent`

```cmd
cd /d C:\marketplace_agent
git fetch origin
git checkout arena/0c7b2c3d-marketplace-agent
git pull origin arena/0c7b2c3d-marketplace-agent
```

Если вы хотите остаться на своей ветке и только забрать изменения:

```cmd
cd /d C:\marketplace_agent
git fetch origin arena/0c7b2c3d-marketplace-agent
git merge origin/arena/0c7b2c3d-marketplace-agent
```

Если git ругается на локальные изменения `worker.py` / `services\...`:

```cmd
cd /d C:\marketplace_agent
git status
git restore worker.py services bot database maintenance config.py
git pull origin arena/0c7b2c3d-marketplace-agent
```

> `git restore` вернёт только файлы кода. `.env`, база и логи не затрагиваются.

---

## 2. Вариант B. Без git — архивом с GitHub

### 2.1 Скачать архив (PowerShell, без браузера)

```cmd
cd /d %USERPROFILE%\Downloads
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/lucifer737ya-wq/marketplace_agent/archive/refs/heads/arena/0c7b2c3d-marketplace-agent.zip' -OutFile 'marketplace_agent.zip'"
```

Альтернатива — открыть в браузере:
`https://github.com/lucifer737ya-wq/marketplace_agent/archive/refs/heads/arena/0c7b2c3d-marketplace-agent.zip`

### 2.2 Распаковать поверх проекта

Сначала **остановите** бота (раздел 4), затем:

```cmd
cd /d C:\
powershell -NoProfile -Command "Expand-Archive -Force '%USERPROFILE%\Downloads\marketplace_agent.zip' 'C:\_update'"
xcopy /E /Y /I C:\_update\marketplace_agent-arena-0c7b2c3d-marketplace-agent C:\marketplace_agent
```

`xcopy /Y` перезаписывает только файлы из архива. Ваши `.env`, `logs`, `backups`
и `database\store.db` остаются на месте.

### 2.3 Перенос «всей папкой» на другой ПК (флешка / OneDrive)

Если нужно забрать **все файлы вместе с `.env` и базой**:

```cmd
robocopy C:\marketplace_agent E:\marketplace_agent /E /XD venv .venv logs\listings_browser_profile logs\ozon_browser_profile .git /XF *.pyc
```

- `/XD` исключает `venv` (его нельзя переносить между ПК — пересоздаётся),
  `.git` и тяжёлые профили браузера;
- `.env`, `database\store.db`, `backups` копируются;
- на новом ПК выполните `setup_env.bat` — он создаст `venv` заново.

---

## 3. Зависимости и браузер (обязательно после обновления)

Если `venv` уже есть:

```cmd
cd /d C:\marketplace_agent
venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
venv\Scripts\python.exe -m playwright install chromium
```

Если `venv` нет или хотите одной командой всё сразу:

```cmd
cd /d C:\marketplace_agent
setup_env.bat
```

`setup_env.bat` создаёт окружение на Python **3.11 или 3.12** (на 3.13
зафиксированные версии зависимостей не гарантированы), ставит пакеты,
скачивает Chromium, создаёт `.env` из шаблона и запускает диагностику.

---

## 4. Остановить бота

```cmd
cd /d C:\marketplace_agent
stop_agent.bat
```

`stop_agent.bat` завершает только процессы, чья командная строка указывает на
`supervisor.py` или `worker.py` **в этой папке**, и удаляет
`logs\restart_worker.signal`. Другие Python-программы на ПК не затрагиваются.

Если нужно остановить только воркер, оставив супервизор (он поднимет новый):

```cmd
cd /d C:\marketplace_agent
del /Q logs\restart_worker.signal 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*marketplace_agent*worker.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
```

---

## 5. Диагностика перед запуском

```cmd
cd /d C:\marketplace_agent
doctor.bat
```

Или без паузы:

```cmd
venv\Scripts\python.exe doctor.py
venv\Scripts\python.exe doctor.py --online
```

Что проверяется:

1. Python 3.11/3.12 и запуск именно из `venv`.
2. Все пакеты из `requirements.txt`.
3. Наличие `.env`, обязательные ключи, формат токена Telegram.
4. Права на запись в `logs\`, `backups\`, `database\`.
5. Целостность `store.db` (`PRAGMA integrity_check`).
6. Синтаксис **всех** `.py` файлов проекта.
7. Маршруты `telegram / ozon / wb / gemini` (прямой + прокси).
8. Наличие Chromium для Playwright.
9. `--online`: реальные `getMe` Telegram и запрос к Gemini.

---

## 6. Запустить скрытый (тихий) режим

```cmd
cd /d C:\marketplace_agent
wscript.exe start_hidden.vbs
```

Или вручную без окна:

```cmd
cd /d C:\marketplace_agent
start "" /B venv\Scripts\pythonw.exe supervisor.py
```

`start_hidden.vbs` запускает `supervisor.py` через `pythonw.exe` — без консоли.
Если `venv` или `supervisor.py` не найдены, скрипт показывает окно с инструкцией,
а не «молча ничего не делает».

Супервизор держит единственный экземпляр (TCP-порт `47831`) и перезапускает
воркер при падении. При повторных неудачах включается пауза 5 → 10 → 20 … 300 с,
чтобы не молотить перезапусками при битом `.env` или занятом порте.

---

## 7. Проверить, что поднялось

```cmd
cd /d C:\marketplace_agent
type logs\heartbeat.json
tasklist | findstr /I "pythonw python"
```

В `heartbeat.json` должно быть свежее `iso_time` (не старше 2 минут),
`"status": "running"` и живой `pid`.

Если процесс есть, а heartbeat старый — смотрите логи:

```cmd
cd /d C:\marketplace_agent
powershell -NoProfile -Command "Get-Content logs\worker.log -Tail 60"
powershell -NoProfile -Command "Get-Content logs\supervisor.log -Tail 60"
```

---

## 8. Автозапуск при включении ПК

```cmd
cd /d C:\marketplace_agent
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\marketplace_agent.lnk'); $s.TargetPath='C:\marketplace_agent\start_hidden.vbs'; $s.WorkingDirectory='C:\marketplace_agent'; $s.Save()"
```

Убрать автозапуск:

```cmd
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\marketplace_agent.lnk"
```

---

## 9. Если что-то не работает

| Симптом | Причина | Решение |
|---|---|---|
| `start_hidden.vbs` показал окно «Не найдено venv» | окружение не создано или папка перенесена без `venv` | `setup_env.bat` |
| В `worker.log`: `Не заполнены обязательные параметры .env` | нет `.env` или ключей | `copy .env.example .env` → `notepad .env` → `doctor.bat` |
| `Telegram недоступен ни по одному маршруту` | нет интернета / заблокирован прямой доступ / мёртв прокси | проверьте `ALLOW_DIRECT_CONNECTIONS=true`; при блокировке поднимите локальный прокси на `127.0.0.1:3066` или укажите свой в `LOCAL_PROXY_URL` |
| `Получен Telegram 409` | тот же токен уже опрашивает другой процесс/ПК | остановите второй экземпляр, затем `stop_agent.bat` и запустите снова |
| `Порт worker занят` | остался старый воркер | `stop_agent.bat`, проверьте `tasklist`, запустите снова |
| Отзывы/конкуренты: «браузерный разбор недоступен» | не скачан Chromium | `venv\Scripts\python.exe -m playwright install chromium` |
| WB: тарифы `0 категорий / 0 складов` | API тарифов временно недоступен | остатки и цены всё равно синхронизируются; повторите позже |
| P&L: «неполное покрытие периода» | Ozon ещё не закрыл период | это штатное поведение: итоги взяты из суточных начислений |
| В журнале `message to edit not found` | старая версия `worker.py` | обновите код до `main`; в актуальной версии статус отправляется заново |
| `maximum number of running instances reached` | прежний цикл автоответов не успел за 30 минут | обновите код: добавлен бюджет 20 минут и лимит 5 ответов за цикл |
| `WB HTTP 404 /api/v1/supplier/stocks` | WB отключил legacy-метод остатков | штатный переход на `analytics/warehouse_remains`; в новой версии повторный запрос — не чаще раза в 6 часов |
| В журнале `РћС€РёР±РєР°` вместо русского текста | UTF-8 журнал прочитан PowerShell в cp1251 | `powershell -NoProfile -Command "Get-Content logs\worker.log -Tail 60 -Encoding UTF8"` |

---

## 10. Что проверить в боте после перезапуска

1. **🏆 ТОП-5 и Убыточные** — рейтинг приходит и за свежую неделю; в шапке указан источник.
2. **📦 P&L отчет (Месяц)** — при незакрытых актах приходит пояснение, а не ошибка.
3. **🔎 Пробив конкурентов** — HTTP → браузер → явный статус «витрина закрыта антиботом».
4. **🔍 Аудит API** — состояние маршрутов Ozon, WB, Gemini и карантинов.
5. **🔄 Синхронизировать МП** — сообщает результат или причину ошибки.
6. **🛠 Самопроверка и автопочинка** — должна ответить «ошибок не обнаружено».
7. `logs\worker.log` — не должно быть `Traceback` после старта.
