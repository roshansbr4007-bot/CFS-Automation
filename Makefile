# Shortcuts for macOS/Linux. Run from the repository root. Windows: see README.
PY ?= backend/.venv/bin/python

.PHONY: db setup-backend setup-frontend migrate admin run-backend run-frontend run test-backend test-frontend e2e lint schema

db:
	docker compose up -d db

setup-backend:
	cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements/dev.txt

setup-frontend:
	cd frontend && if [ -f package-lock.json ]; then npm ci; else npm install; fi

migrate:
	cd backend && .venv/bin/python manage.py migrate

admin:
	cd backend && .venv/bin/python manage.py create_initial_admin

run-backend:
	cd backend && .venv/bin/python manage.py runserver 8000

run-frontend:
	cd frontend && npm run dev

run:
	$(MAKE) db
	$(MAKE) -j2 run-backend run-frontend

test-backend:
	cd backend && .venv/bin/pytest --cov

test-frontend:
	cd frontend && npm test

e2e:
	cd frontend && npx playwright test

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd frontend && npm run lint && npm run typecheck

schema:
	cd backend && .venv/bin/python manage.py spectacular --validate --fail-on-warn --file schema.yml
