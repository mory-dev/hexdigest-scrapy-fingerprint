# Contributing

Bug reports, documentation improvements, and focused pull requests are
welcome. Please run the local checks before opening a pull request:

```bash
python -m pip install -e ".[test]"
pytest
ruff check .
```

Tests must use local fakes or servers and must not contact a marketplace,
third-party proxy, or external URL. Keep proxy credentials out of logs,
fixtures, and issue reports. Changes to retry behavior should include a test
for refusal responses and for the no-loop guarantee.
