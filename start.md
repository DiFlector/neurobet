# Prod — старт
cd /srv/neurobet && docker compose up --build -d

# Prod — стоп
cd /srv/neurobet && docker compose down

# Dev — старт
cd /srv/neurobet && docker compose -f docker-compose.yml -f docker-compose.dev.yml -p neurobet-dev --env-file .env.dev up --build -d

# Dev — стоп
cd /srv/neurobet && docker compose -f docker-compose.yml -f docker-compose.dev.yml -p neurobet-dev --env-file .env.dev down
