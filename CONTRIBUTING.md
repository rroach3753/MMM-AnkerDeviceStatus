# Contributing

Bug reports and improvements are welcome. Before opening an issue, check
existing reports and remove credentials, tokens, serial numbers, and other
private account or device data from logs and screenshots.

For code changes:

1. Keep changes focused and update relevant documentation.
2. Install Python dependencies from `requirements.txt` in a virtual
   environment.
3. Run the syntax, test, and lint commands in the [README](./README.md), and
   `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
4. Describe which devices and runtime versions you actually tested. Do not
   claim live-device behavior from mocked or automated tests alone.

Never include account credentials, authentication caches, or unredacted
Anker API/MQTT payloads in a contribution.

By participating, you agree to follow the project's
[Code of Conduct](./CODE_OF_CONDUCT.md).
