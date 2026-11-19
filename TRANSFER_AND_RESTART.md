# Перенос файлов на ПК и перезапуск скрытого режима

Все команды ниже вводятся в **cmd** (`Win+R` → `cmd` → Enter). Копируйте блоками, по одному.

---

## Вариант A. Git на ПК уже настроен (предпочтительно)

```cmd
cd /d C:\
git clone https://github.com/lucifer737ya-wq/marketplace_agent.git marketplace_agent_new
cd /d C:\marketplace_agent_new
git checkout arena/9b80520f-marketplace-agent
```

Если папка `marketplace_agent` уже есть и в ней ваш рабочий `.env`:

```cmd
cd /d C:\marketplace_agent
git fetch origin
git checkout arena/9b80520f-marketplace-agent
git pull origin arena/9b80520f-marketplace-agent
```

`.env`, `logs\`, `backups\` и база `*.db` в git не входят — они остаются на месте и **не перезаписываются**.
Если хотите остаться на своей ветке и только забрать изменения:

```cmd
cd /d C:\marketplace_agent
git fetch origin arena/9b80520f-marketplace-agent
git merge origin/arena/9b80520f-marketplace-agent
```

---

## Вариант B. Без git — перенос папкой

1. На машине, где лежит свежая копия, получите архив с сайта GitHub:
   `https://github.com/lucifer737ya-wq/marketplace_agent/archive/refs/heads/arena/9b80520f-marketplace-agent.zip`
2. Скопируйте zip на ПК (флешка, OneDrive, Telegram «Избранное»).
3. Распакуйте **поверх** папки проекта, чтобы `worker.py`, `supervisor.py`, `services\`, `bot\` заменились:

```cmd
cd /d C:\
powershell -Command "Expand-Archive -Force '%USERPROFILE%\Downloads\marketplace_agent-arena-9b80520f-marketplace-agent.zip' 'C:\_update'"
xcopy /E /Y /I C:\_update\marketplace_agent-arena-9b80520f-marketplace-agent C:\marketplace_agent
```

> Внимание: `xcopy /Y` перезаписывает только файлы из архива. Ваши `.env`, `logs`, `backups` и база данных не в архиве — они сохранятся.
> Перед обновлением закройте бота (см. «Остановить» ниже).

---

## Обновить зависимости и браузер (обязательно один раз после обновления)

```cmd
cd /d C:\marketplace_agent
venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
venv\Scripts\python.exe -m playwright install chromium
```

`playwright install chromium` нужен для разбора витрин конкурентов и автоответов на отзывы.
Если браузер уже стоял — команда просто подтвердит, что он на месте.

Если `venv` ещё нет:

```cmd
cd /d C:\marketplace_agent
py -3.11 -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m playwright install chromium
```

---

## Остановить бота

```cmd
cd /d C:\marketplace_agent
taskkill /F /IM pythonw.exe /T
taskkill /F /IM python.exe /T
```

Если хотите остановить только воркер, оставив супервизор, удалите файлы-сигналы:

```cmd
del /Q C:\marketplace_agent\logs\restart.signal 2>nul
```

---

## Запустить скрытый режим

```cmd
cd /d C:\marketplace_agent
wscript.exe start_hidden.vbs
```

Или вручную без окна:

```cmd
cd /d C:\marketplace_agent
start "" /B venv\Scripts\pythonw.exe supervisor.py
```

`start_hidden.vbs` запускает `supervisor.py` через `pythonw.exe` — без консольного окна.
Супервизор держит единственный экземпляр (блокировка `logs\worker.pid`) и перезапускает воркер при падении.

---

## Проверить, что поднялось

```cmd
cd /d C:\marketplace_agent
type logs\heartbeat.json
tasklist | findstr /I "pythonw python"
```

В `heartbeat.json` должно быть свежее время (`iso_time` не старше 2 минут), `"status": "running"` и живой `pid`.
Если процесс есть, а heartbeat старый — смотрите `logs\worker.log`:

```cmd
cd /d C:\marketplace_agent
powershell -Command "Get-Content logs\worker.log -Tail 60"
```

---

## Автозапуск при включении ПК

Положите ярлык на `start_hidden.vbs` в папку автозагрузки:

```cmd
cd /d C:\marketplace_agent
powershell -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\marketplace_agent.lnk'); $s.TargetPath='C:\marketplace_agent\start_hidden.vbs'; $s.WorkingDirectory='C:\marketplace_agent'; $s.Save()"
```

Убрать автозапуск:

```cmd
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\marketplace_agent.lnk"
```

---

## Что проверить в боте после перезапуска

1. **🏆 ТОП-5 и Убыточные** — теперь приходит рейтинг даже за свежую неделю. В шапке будет строка про источник («начисления by-day» или «резервный источник»).
2. **📦 P&L отчет (Месяц)** — если закрывающие акты ещё не сформированы, вместо ошибки придёт понятное пояснение.
3. **🔎 Пробив конкурентов** — сначала пробует HTTP, затем браузер. Если витрина закрыта антиботом, скажет об этом прямо.
4. **🔍 Аудит API** — показывает состояние маршрутов Ozon, WB и Gemini, задержку Gemini и статус карантинов.
5. **🔄 Синхронизировать МП** — теперь сообщает о результате или об ошибке, а не молчит.

Если задача в планировщике упадёт, в Telegram придёт сообщение `⚠️ Фоновая задача <имя> завершилась ошибкой` — раньше такие сбои пропадали в логе.
