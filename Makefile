.PHONY: test
test:
	PYTHONPATH=scripts python3 -m unittest discover -s tests -p 'test_*.py' -v

.PHONY: infrastructure-check preflight
infrastructure-check:
	terraform -chdir=ci fmt -check -recursive
	terraform -chdir=ci init -backend=false -input=false -lockfile=readonly
	terraform -chdir=ci validate
	terraform -chdir=ci test

preflight: test infrastructure-check
	python3 scripts/plan_preflight.py
