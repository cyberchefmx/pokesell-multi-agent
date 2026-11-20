.PHONY: install test eval web adk-web cli terraform-check

PYTHON ?= .venv/bin/python

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -v

eval:
	$(PYTHON) -m eval.run_evaluation

web:
	POKESELL_BIND_HOST=127.0.0.1 POKESELL_BIND_PORT=8080 $(PYTHON) -m pokesell_agent.web_server

adk-web:
	.venv/bin/adk web .

cli:
	$(PYTHON) -m pokesell_agent.cli

terraform-check:
	terraform -chdir=terraform fmt -check
	terraform -chdir=terraform init -backend=false
	terraform -chdir=terraform validate
