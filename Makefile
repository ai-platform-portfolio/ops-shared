.PHONY: test
test:
	PYTHONPATH=scripts python3 -m unittest discover -s tests -p 'test_*.py' -v

.PHONY: infrastructure-check preflight
infrastructure-check:
	tofu -chdir=ci fmt -check -recursive
	tofu -chdir=ci init -backend=false -input=false -lockfile=readonly
	tofu -chdir=ci validate
	tofu -chdir=ci test

preflight: test infrastructure-check
	python3 scripts/plan_preflight.py
