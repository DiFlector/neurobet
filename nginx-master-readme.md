# Документация: Архитектура и работа с `nginx-master`

В данном документе собрана вся информация о топологии сети, конфигурации обратного прокси `nginx-master`, правилах добавления новых проектов и процедуре обновления SSL-сертификатов.

---

## 1. Топология сети и инфраструктура

| Хост / IP | Назначение | Доступ / Пользователь | Роль в системе |
| :--- | :--- | :--- | :--- |
| **`necrolich.ru`** (`192.168.1.92`) | Сервер приложений | SSH: `diflector` (sudo, docker, pulse) | Здесь крутятся ваши контейнеры в `/srv/` (`obsidian`, `dftgbot`, `minecraft` и др.). |
| **`192.168.1.90`** | Хост `nginx-master` | SSH с `192.168.1.92`: `sshpass -p "Zz128500246315" diflector@192.168.1.90` (группа `docker`) | Единая точка входа по внешнему белому IP `46.138.240.195`. Принимает порты 80, 443, 4443. |

---

## 2. Архитектура `nginx-master` на `192.168.1.90`

Контейнер обратного прокси запущен в Docker под именем **`nginx_master`** (образ `nginx:1.27-alpine`).

### Смонтированные директории:
* **Конфигурация:** `/srv/nginx-master/nginx/nginx.conf` → `/etc/nginx/nginx.conf:ro`
* **Сниппеты:** `/srv/nginx-master/nginx/snippets` → `/etc/nginx/snippets:ro`
* **Корневая статика (Webroot):** `/srv/nginx-master/nginx/site` → `/etc/nginx/site`
* **SSL-сертификаты:** `/srv/pulsePython/nginx/certs` → `/etc/nginx/certs:ro`

### Структура сниппетов в `/srv/nginx-master/nginx/snippets/`:
* **`proxy-common.conf`** — базовый конфиг проксирования, подключен во всех локейшенах:
  * Проброс заголовков `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`.
  * Поддержка WebSockets (`Upgrade $http_upgrade`, `Connection $connection_upgrade`).
  * **Уже содержит `proxy_buffering off;` и `proxy_redirect off;`** (дублировать их в своих локациях нельзя!).
* **`diflector-locations.conf`** — сниппет для всех проектов пользователя `diflector` на домене `necrolich.ru`:
  * Подключен внутри `server { server_name necrolich.ru www.necrolich.ru; listen 443 ssl; ... }`.
  * Содержит пути: `/diflector/neurobet`, `/diflector/bot/`, `/diflector/obsidian/`.

---

## 3. Как пробрасывать новые проекты через `diflector-locations.conf`

### Шаблон добавления нового сервиса:
Если на машине `192.168.1.92` запущен сервис на порту (например, `5984` или `8000`):

```nginx
# --- Название сервиса ---
location = /diflector/myproject {
    return 301 /diflector/myproject/;
}

location /diflector/myproject/ {
    auth_basic off;
    # Если бэкенд не знает о префиксе /diflector/myproject/ — отсекаем его:
    rewrite ^/diflector/myproject/(.*) /$1 break;
    
    proxy_pass http://192.168.1.92:ПОРТ;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_read_timeout 300s;
    client_max_body_size 64m;
}
```

### Как безопасно применить конфиг:
Поскольку у пользователя `diflector` на `192.168.1.90` есть доступ к Docker без sudo, правки в файл сниппета вносятся через helper-контейнер Alpine:

```bash
# 1. С сервера necrolich.ru подключаемся к 192.168.1.90:
sshpass -p "Zz128500246315" ssh diflector@192.168.1.90

# 2. Добавляем блок в конец /srv/nginx-master/nginx/snippets/diflector-locations.conf
# 3. Проверяем синтаксис Nginx:
docker exec nginx_master nginx -t

# 4. Мягко применяем изменения:
docker exec nginx_master nginx -s reload
```

---

## 4. Обновление и перевыпуск SSL-сертификатов (Let's Encrypt)

Для домена `necrolich.ru` настроен endpoint для валидации HTTP-01 ACME Challenge в блоке `listen 80`:
```nginx
location ^~ /.well-known/acme-challenge/ {
    root /etc/nginx/site;
}
```

### Команда для выпуска / продления сертификата:
Выполняется на хосте `192.168.1.90`:

```bash
# 1. Запуск Certbot в Docker с webroot /site:
docker run --rm \
  -v /srv/nginx-master/nginx/site:/site \
  -v /srv/pulsePython/nginx/certs/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly \
  --webroot -w /site \
  -d necrolich.ru -d www.necrolich.ru \
  --agree-tos \
  --register-unsafely-without-email \
  --non-interactive

# 2. Копирование полученных файлов в целевые пути Nginx:
docker run --rm -v /srv/pulsePython/nginx/certs:/certs alpine sh -c "
  cp /certs/letsencrypt/live/necrolich.ru/fullchain.pem /certs/necrolich.ru.crt && \
  cp /certs/letsencrypt/live/necrolich.ru/privkey.pem /certs/necrolich.ru.key && \
  chmod 644 /certs/necrolich.ru.crt && chmod 600 /certs/necrolich.ru.key
"

# 3. Перезагрузка Nginx:
docker exec nginx_master nginx -s reload
```

---

## 5. Важные грабли и особенности (Gotchas)

1. **Дублирование `proxy_buffering`**:
   Никогда не добавляйте `proxy_buffering off;` в `diflector-locations.conf`, если подключаете `proxy-common.conf`. Nginx строго падает с ошибкой `proxy_buffering directive is duplicate`.
2. **Docker Bind Mount Inode (очень важно!)**:
   Файл `/srv/nginx-master/nginx/nginx.conf` смонтирован как одиночный файл. 
   Если на хосте выполнить `sed -i` над `nginx.conf`, Linux создаст новый inode файла, и запущенный контейнер Docker **не увидит изменений**, пока его не перезапустить (`docker restart nginx_master`). Редактировать нужно либо внутри существующего файла (`cat new > file`), либо перезапускать контейнер после `sed -i`.
3. **Отсечение префиксов (`rewrite ... break`)**:
   Для сервисов, которые ожидают запросы в корень `/` (например, CouchDB или бэкенд без basePath), обязательна директива:
   `rewrite ^/diflector/<name>/(.*) /$1 break;` перед `proxy_pass`.
4. **Снятие Basic Auth**:
   Если в `nginx-master` глобально включена авторизация `auth_basic`, для публичных API или клиентских плагинов (как LiveSync) обязательно указывайте `auth_basic off;`.
