#!/usr/bin/env bash
set -e

echo "=== 1. Syncing packages/features and services/neural to containers ==="
docker cp /srv/neurobet/packages/features/src/features/selection.py neurobet_neural:/usr/local/lib/python3.12/site-packages/features/selection.py
docker cp /srv/neurobet/packages/features/src/features/__init__.py neurobet_neural:/usr/local/lib/python3.12/site-packages/features/__init__.py

docker cp /srv/neurobet/packages/features/src/features/selection.py neurobet_backend:/usr/local/lib/python3.12/site-packages/features/selection.py
docker cp /srv/neurobet/packages/features/src/features/__init__.py neurobet_backend:/usr/local/lib/python3.12/site-packages/features/__init__.py

docker cp /srv/neurobet/services/neural/app/models.py neurobet_neural:/app/app/models.py
docker cp /srv/neurobet/services/neural/app/dataset.py neurobet_neural:/app/app/dataset.py
docker cp /srv/neurobet/services/neural/app/inference.py neurobet_neural:/app/app/inference.py

echo "=== 2. Running Anti-Overfitting Unit Tests ==="
docker exec -i neurobet_neural python - < /srv/neurobet/tests/neural/test_feature_pruning.py
docker exec -i neurobet_neural python - < /srv/neurobet/tests/neural/test_residual_estimator.py
docker exec -i neurobet_neural python - < /srv/neurobet/tests/neural/test_purged_folds.py

echo "=== 3. Running Strategy & Integration Regression Tests ==="
docker exec -i neurobet_neural python - < /srv/neurobet/tests/neural/test_tennis_strategy.py
docker exec -i neurobet_neural python - < /srv/neurobet/tests/neural/test_set_match.py
docker exec -i neurobet_backend python - < /srv/neurobet/tests/contract/test_contracts.py

echo "=== Anti-Overfitting ML Engine verified successfully! ==="
