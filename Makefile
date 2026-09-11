.PHONY: help install dev test lint format migrate shell docker-up docker-down clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -------- Setup --------
install:  ## Install dependencies (dev mode)
	pip install -e ".[dev]"

install-all:  ## Install all optional dependencies
	pip install -e ".[all,dev]"

install-playwright:  ## Install Playwright browsers
	playwright install chromium

env:  ## Copy .env.example → .env (if .env doesn't exist)
	@test -f .env || (cp .env.example .env && echo "Created .env — please fill in your API keys")

# -------- Development --------
dev:  ## Start VoxOS in development mode
	uvicorn voxos.main:app --host 0.0.0.0 --port 8000 --reload --log-level info

# -------- Database --------
migrate:  ## Run Alembic migrations
	alembic upgrade head

migrate-create:  ## Create a new migration (usage: make migrate-create name="add_table_x")
	alembic revision --autogenerate -m "$(name)"

migrate-down:  ## Roll back last migration
	alembic downgrade -1

db-shell:  ## Connect to PostgreSQL
	docker exec -it voxos_postgres psql -U $${POSTGRES_USER:-voxos} -d $${POSTGRES_DB:-voxos}

# -------- Testing --------
test:  ## Run all tests
	pytest tests/ -v --cov=voxos --cov-report=term-missing

test-unit:  ## Run unit tests only
	pytest tests/unit/ -v

test-security:  ## Run security tests only
	pytest tests/security/ -v

test-fast:  ## Run tests, skip slow/integration
	pytest tests/ -v -m "not slow and not integration"

# -------- Linting --------
lint:  ## Lint with ruff
	ruff check voxos/ tests/

format:  ## Format code with ruff
	ruff format voxos/ tests/

typecheck:  ## Type-check with mypy
	mypy voxos/

# -------- Docker --------
docker-up:  ## Start PostgreSQL + Redis
	docker compose up -d postgres redis
	@echo "Waiting for services to be ready..."
	@sleep 3
	@echo "PostgreSQL: localhost:5432"
	@echo "Redis:      localhost:6379"

docker-down:  ## Stop all Docker services
	docker compose down

docker-logs:  ## Tail Docker service logs
	docker compose logs -f

docker-reset:  ## Reset Docker volumes (DESTROYS DATA)
	docker compose down -v

# -------- Utilities --------
clean:  ## Remove Python cache files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +

shell:  ## Print loaded settings
	python -c "from voxos.config import get_settings; print(get_settings().model_dump())"

status:  ## Show VoxOS service status
	@curl -s http://localhost:8000/api/v1/system/status | python -m json.tool 2>/dev/null || echo "VoxOS not running. Run: make dev"
