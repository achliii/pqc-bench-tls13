# Atajos de PQC-Bench TLS 1.3. Todos los comandos se pueden ejecutar también a mano (ver README).
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)
CLIENT := docker compose exec -e HOST_UID=$(HOST_UID) -e HOST_GID=$(HOST_GID) pqc-client-orchestrator

.PHONY: up down bench exp plots mock test test-docker logs shell

up:            ## Construye y levanta servidor y cliente
	docker compose up -d --build

down:          ## Detiene y elimina los contenedores
	docker compose down

bench: up      ## Campaña completa EXP_01..EXP_08 + figuras
	$(CLIENT) python bench/run_experiments.py --plot

exp: up        ## Un experimento: make exp EXP=EXP_04
	$(CLIENT) python bench/run_experiments.py --exp $(EXP)

plots:         ## Regenera figuras y tabla desde ./output
	$(CLIENT) python bench/analytics_plotter.py

mock:          ## Datos simulados en ./output_mock + figuras (sin Docker)
	python3 bench/mock_telemetry.py --output-dir output_mock
	python3 bench/analytics_plotter.py --input-dir output_mock

test:          ## Pruebas unitarias (host, requiere pytest/pandas/matplotlib)
	python3 -m pytest -q tests

test-docker: up ## Pruebas unitarias dentro del contenedor cliente
	docker compose exec pqc-client-orchestrator sh -c "pip install -q pytest && python -m pytest -q -p no:cacheprovider tests"

logs:
	docker compose logs -f pqc-server

shell:
	docker compose exec pqc-client-orchestrator bash
