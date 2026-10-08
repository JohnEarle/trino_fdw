.PHONY: venv test integration image clean

venv:
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[test]"

test:
	.venv/bin/python -m pytest -q

integration:
	dev/integration.sh

image:
	docker build -f image/Dockerfile -t cnpg-trino-fdw:17 .

clean:
	cd dev && docker compose down -v --remove-orphans; rm -rf dev/.generated
