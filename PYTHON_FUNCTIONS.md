# Python custom functions

Пользовательские функции поддерживают `kind: "python"`.

Реализация хранится в `implementation.inline_code` и должна содержать функцию
с тем же именем, что и `name`:

```json
{
  "kind": "python",
  "name": "create_file",
  "implementation": {
    "inline_code": "import os\n\ndef create_file(folder_path, file_name, content):\n    os.makedirs(folder_path, exist_ok=True)\n    with open(os.path.join(folder_path, file_name), 'w', encoding='utf-8') as f:\n        f.write(content)\n    return {'ok': True}"
  }
}
```

Порядок работы:

1. LLM вызывает `propose_function`.
2. Функция сохраняется как `draft` и код не запускается.
3. Владелец нажимает `проверить`.
4. После успешной проверки владелец нажимает `подтвердить и активировать`.
5. Только активная Python-функция попадает в tool schemas и может быть вызвана моделью.
6. При вызове код запускается в отдельном процессе Python.

Ограничения процесса: таймаут по умолчанию 30 секунд (максимум 120), ограничение
размера stdout 200000 символов (максимум 1000000).

ВАЖНО: это не полноценный security sandbox. Активированный Python-код получает
права ОС того пользователя, под которым запущен PAI, и может импортировать
доступные Python-модули и работать с файлами/сетью. Поэтому `activate` является
явным разрешением на выполнение кода.
