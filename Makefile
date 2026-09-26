.PHONY: test
test:
	PYTHONPATH=scripts python3 -m unittest discover -s tests -p 'test_*.py' -v
