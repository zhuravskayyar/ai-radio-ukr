# Vector Radio Online

Онлайн-режим запускає той самий інтерфейс Vector Radio у Chrome, Edge, Firefox,
Safari, Android та iOS. Локальна Windows-версія продовжує працювати без змін.

## Швидкий запуск у локальній мережі

Запустіть `Start Vector Radio Online.cmd` або виконайте:

```powershell
.venv311\Scripts\python.exe online.py --host 0.0.0.0 --port 8080 --public-listen --allowed-origin https://zhuravskayyar.github.io
```

У консолі з'являться дві адреси:

- звичайна адреса — публічний режим слухача;
- адреса з `#token=...` — повна панель адміністратора.

Фрагмент після `#` не надсилається серверу й не потрапляє до HTTP-журналу.
Після першого успішного запиту браузер отримує короткочасну `HttpOnly`-сесію,
а токен прибирається з адреси.

Інші пристрої у Wi-Fi відкривають `http://IP-КОМП'ЮТЕРА:8080/ui/index.html`.
За потреби дозвольте вхідний TCP-порт 8080 у Windows Firewall лише для
приватної мережі.

## Приватний режим

Без `--public-listen` навіть прослуховування потребує токена:

```powershell
$env:VECTOR_RADIO_ADMIN_TOKEN = "довгий-випадковий-секрет-щонайменше-16-символів"
.venv311\Scripts\python.exe online.py --host 127.0.0.1 --port 8080
```

Підтримувані змінні середовища:

- `VECTOR_RADIO_HOST` і `VECTOR_RADIO_PORT`;
- `VECTOR_RADIO_ADMIN_TOKEN`;
- `VECTOR_RADIO_PUBLIC_LISTEN=1`;
- `VECTOR_RADIO_SECURE_COOKIE=1` для HTTPS.
- `VECTOR_RADIO_ALLOWED_ORIGINS=https://zhuravskayyar.github.io` для GitHub Pages.

## GitHub Pages

Публічний клієнт розміщується за адресою
`https://zhuravskayyar.github.io/ai-radio-ukr/ui/`. Головна сторінка репозиторію
містить кнопку «Слухати онлайн».

GitHub Pages є статичним хостингом: він не запускає `online.py`, не має доступу
до SQLite, локальної музики або API-ключів. Тому під час першого відкриття
введіть HTTPS-адресу запущеного Vector Radio Server. Вона зберігається тільки
у `localStorage` цього браузера. Також адресу можна передати посиланням:

```text
https://zhuravskayyar.github.io/ai-radio-ukr/ui/?server=https%3A%2F%2Fradio.example.com
```

Коли з'явиться постійний домен сервера, його можна прописати один раз у
`ui/online-config.js` як `apiBase`; після цього слухачі не бачитимуть форму
підключення. Сервер повинен бути запущений з `--public-listen` і точним
`--allowed-origin https://zhuravskayyar.github.io`.

## Публікація в інтернеті

Не відкривайте вбудований HTTP-сервер безпосередньо в інтернет. Розмістіть
його за HTTPS reverse proxy (Caddy, Nginx, Cloudflare Tunnel або аналогом),
залишивши Python на `127.0.0.1:8080`, і встановіть:

```powershell
$env:VECTOR_RADIO_ADMIN_TOKEN = "окремий-довгий-випадковий-секрет"
$env:VECTOR_RADIO_PUBLIC_LISTEN = "1"
$env:VECTOR_RADIO_SECURE_COOKIE = "1"
$env:VECTOR_RADIO_ALLOWED_ORIGINS = "https://zhuravskayyar.github.io"
.venv311\Scripts\python.exe online.py
```

Reverse proxy повинен передавати `Host`, підтримувати довгі відповіді й не
видаляти заголовки `Range`, `Content-Range`, `Set-Cookie` та
`X-Content-Type-Options`. Окремий CORS не потрібен: API навмисно приймає лише
same-origin запити.

## Що підтримується

- MP3, FLAC, M4A, AAC, WAV, OGG, Opus і WebM із перемотуванням через HTTP Range;
- публічне прослуховування без доступу до налаштувань та API-ключів;
- повна авторизована панель: бібліотека, AI-підводки, TTS, черга, аналітика й
  ефірна безпека;
- адаптивний інтерфейс і встановлення як PWA;
- локальні обкладинки та резервні YouTube-прев'ю;
- один код інтерфейсу для pywebview і звичайного браузера.

Windows EXE-оновлення в online-процесі вимкнене навмисно. Сервер оновлюється
розгортанням нової версії коду, тоді як кнопка оновлення залишається доступною
у desktop-застосунку.

## Перевірка

```powershell
.venv\Scripts\python.exe -m unittest tests.test_online_server -v
```

Сервер не роздає корінь репозиторію, `api.txt`, SQLite, логи або довільні
локальні шляхи. Аудіо й обкладинки доступні лише через ID треку з бази.
