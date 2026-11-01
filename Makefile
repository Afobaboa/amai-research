PYTHON ?= python3
PORT ?= 8000

.PHONY: help setup setup-browser init-demo upgrade-demo dev check test test-backend test-frontend test-frontend-headed

help:
	@echo "make setup                Python, миграции и локальная демобаза"
	@echo "make dev                  Фронт и бэкенд на http://127.0.0.1:8000"
	@echo "make dev PORT=8001        Запуск на другом порту"
	@echo "make upgrade-demo         Копия базы, миграции и зимние учебные примеры"
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

init-demo:
	$(PYTHON) scripts/local.py init-demo

upgrade-demo:
	$(PYTHON) scripts/local.py upgrade-demo

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
