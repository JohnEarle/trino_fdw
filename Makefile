.PHONY: venv test integration clean

venv:
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[test]"

test:
	.venv/bin/python -m pytest -q

integration:
	dev/integration.sh

clean:
	cd dev && docker compose down -v --remove-orphans; rm -rf dev/.generated
