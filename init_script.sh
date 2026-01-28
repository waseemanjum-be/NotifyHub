#!/usr/bin/env bash
set -euo pipefail

#PROJECT_NAME="${1:-fastapi-mongo-blueprint}"
#
#mkdir -p "${PROJECT_NAME}"
#cd "${PROJECT_NAME}"

# ----------------------------
# Directories
# ----------------------------
mkdir -p app/{api/v1,core,db,models,schemas,services,repositories,utils,tests}
mkdir -p scripts docker

# ----------------------------
# Empty files (touch)
# ----------------------------
touch \
  .env.example \
  .gitignore \
  README.md \
  pyproject.toml \
  requirements.txt \
  Dockerfile \
  docker-compose.yml

touch \
  app/__init__.py \
  app/main.py

touch \
  app/api/__init__.py \
  app/api/v1/__init__.py \
  app/api/v1/routes.py \
  app/api/v1/sample.py

touch \
  app/core/__init__.py \
  app/core/config.py \
  app/core/logging.py

touch \
  app/db/__init__.py \
  app/db/mongo.py

touch \
  app/models/__init__.py

touch \
  app/schemas/__init__.py \
  app/schemas/sample.py

touch \
  app/repositories/__init__.py \
  app/repositories/sample_repo.py

touch \
  app/services/__init__.py \
  app/services/sample_service.py

touch \
  app/utils/__init__.py

touch \
  app/tests/__init__.py \
  app/tests/test_health.py

touch \
  scripts/dev.sh \
  scripts/test.sh

echo "✅ Created dirs + empty starter files in: ${PROJECT_NAME}"
