# Access Development Makefile

.PHONY: help install install-python install-node start start-backend start-frontend sync sync-auth sync-groups-auth sync-memberships-auth db-init db-migrate test lint format clean

# Default target
help:
	@echo "Access Development Commands:"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make install          Install all dependencies (Python + Node.js)"
	@echo "  make install-python   Install Python dependencies only"
	@echo "  make install-node     Install Node.js dependencies only"
	@echo ""
	@echo "Development:"
	@echo "  make start            Start both Flask backend and React frontend"
	@echo "  make start-backend    Start Flask backend only"
	@echo "  make start-frontend   Start React frontend only"
	@echo ""
	@echo "Sync Jobs:"
	@echo "  make sync                     Run sync job (non-authoritative)"
	@echo "  make sync-auth                Run sync job (authoritative mode)"
	@echo "  make sync-groups-auth         Run groups sync (authoritative mode)"
	@echo "  make sync-memberships-auth    Run memberships sync (authoritative mode)"
	@echo ""
	@echo "Database:"
	@echo "  make db-init          Initialize database with admin user"
	@echo "  make db-migrate       Run database migrations"
	@echo ""
	@echo "Utilities:"
	@echo "  make test             Run Python tests via tox"
	@echo "  make lint             Run Python linting (ruff + mypy) and TypeScript checks"
	@echo "  make format           Format Python and TypeScript code"
	@echo "  make clean            Clean temporary files"

# Installation targets
install: install-python install-node

install-python:
	@echo "Installing Python dependencies..."
	python3 -m venv venv || true
	. venv/bin/activate && pip install -r requirements.txt -r requirements-test.txt

install-node:
	@echo "Installing Node.js dependencies..."
	@command -v npm >/dev/null 2>&1 || { echo "Error: npm is not installed"; exit 1; }
	npm install

# Development server targets
start:
	@echo "Starting both backend and frontend servers..."
	@echo "Backend will be available at http://localhost:6060"
	@echo "Frontend will be available at http://localhost:3000"
	@echo "Press Ctrl+C to stop both servers"
	@trap 'kill 0' INT; \
	(. venv/bin/activate && flask run) & \
	npm start & \
	wait

start-backend:
	@echo "Starting Flask backend server..."
	. venv/bin/activate && flask run

start-frontend:
	@echo "Starting React frontend server..."
	npm start

# Sync job targets
sync:
	@echo "Running sync job (non-authoritative)..."
	. venv/bin/activate && flask sync

sync-auth:
	@echo "Running sync job (authoritative mode)..."
	. venv/bin/activate && flask sync --sync-groups-authoritatively --sync-group-memberships-authoritatively

sync-groups-auth:
	@echo "Running groups sync (authoritative mode)..."
	. venv/bin/activate && flask sync --sync-groups-authoritatively

sync-memberships-auth:
	@echo "Running memberships sync (authoritative mode)..."
	. venv/bin/activate && flask sync --sync-group-memberships-authoritatively

# Database targets
db-init: db-migrate
	@echo "Initializing database..."
	@read -p "Enter admin user email: " EMAIL; \
	. venv/bin/activate && flask init $$EMAIL

db-migrate:
	@echo "Running database migrations..."
	. venv/bin/activate && flask db upgrade

# Utility targets
test:
	@echo "Running tests..."
	. venv/bin/activate && tox -e test

lint:
	@echo "Running Python linting..."
	. venv/bin/activate && tox -e ruff
	@echo "Running Python type checking..."
	. venv/bin/activate && tox -e mypy
	@echo "Running TypeScript checks..."
	npm run tsc

format:
	@echo "Formatting Python code..."
	. venv/bin/activate && ruff format .
	@echo "Formatting TypeScript code..."
	npx prettier --write .

clean:
	@echo "Cleaning temporary files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache 2>/dev/null || true
	rm -rf node_modules/.cache 2>/dev/null || true
