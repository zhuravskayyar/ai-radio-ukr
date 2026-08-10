# StyleTTS2 Ukrainian — локальне встановлення

LUMEN Radio використовує `patriotyk/styletts2_ukrainian_single` як основний
локальний український голос. Edge TTS і системний `uk-UA` голос залишаються
автоматичними резервами.

## Робоче середовище

Проєкт запускає Python 3.11 із `.venv311`. Скрипт `Start LUMEN Radio.cmd`
автоматично надає цьому середовищу пріоритет.

```powershell
.\.venv311\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

CPU підтримується. CUDA, якщо вона доступна у встановленому PyTorch,
використовується автоматично.

## Модель

Windows Setup завантажує й перевіряє ці файли під час встановлення. У середовищі розробника вони автоматично завантажуються під час першого локального синтезу:

- `patriotyk/styletts2_ukrainian_single/pytorch_model.bin` (приблизно 749 MB);
- `config.yml`;
- голосовий prompt `filatov.pt` з офіційного StyleTTS2 Ukrainian Space.
- українські `stanza` ресурси для коректного визначення наголосів (у
  `cache/stanza`).

Після встановлення синтез працює локально. Стан рушія видно в розділі
`Налаштування → Український голос`.

## Перевірка

```powershell
.\.venv311\Scripts\python.exe -c "from backend.tts_styletts import styletts_status; print(styletts_status())"
```

End-to-end тест через той самий API, який викликає радіо:

```powershell
.\.venv311\Scripts\python.exe scripts\smoke_styletts.py
```

Локальний результат кешується як справжній 24 kHz mono WAV. Edge TTS має
окремий MP3-кеш, тому старі Edge-файли не перекривають StyleTTS2.
