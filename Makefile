PYTHON ?= python3
PORT ?= 8000

.PHONY: help setup setup-browser init-data upgrade-data init-demo upgrade-demo dev check test test-backend test-frontend test-frontend-headed

help:
	@echo "make setup                Python, миграции и данные из Excel"
	@echo "make dev                  Фронт и бэкенд на http://127.0.0.1:8000"
	@echo "make dev PORT=8001        Запуск на другом порту"
	@echo "make init-data            Миграции, сотрудники и реальные зимние рецептуры"
	@echo "make upgrade-data         Копия базы, миграции и замена демоданных"
	@echo "make setup-browser        Playwright и Chromium для UI-тестов"
	@echo "make test-backend         Серверные тесты во временной базе"
	@echo "make test-frontend        Браузерные тесты во временной базе"
	@echo "make test-frontend-headed Браузерные тесты в видимом окне"
	@echo "make test                 Все проверки"

setup:
	$(PYTHON) scripts/local.py setup

setup-browser:
	npm ci
	npx --no-install playwright install chromium

init-data:
	$(PYTHON) scripts/local.py init-data

upgrade-data:
	$(PYTHON) scripts/local.py upgrade-data

init-demo: init-data

upgrade-demo: upgrade-data

dev:
	$(PYTHON) scripts/local.py dev --port $(PORT)

check:
	$(PYTHON) scripts/local.py check

test-backend:
	$(PYTHON) scripts/local.py test-backend

test-frontend:
	npm run test:frontend

test-frontend-headed:
	npm run test:frontend:headed

test: test-backend test-frontend

worker:
	$(PYTHON) scripts/local.py manage assistant_worker
