.PHONY: test quality compile

test:
	cd backend && python -m pytest -q

quality:
	python scripts/run_quality_gate.py

compile:
	cd backend && python -m compileall app tests
