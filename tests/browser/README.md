# Isolated browser smoke test

Install the development requirements plus `playwright==1.63.0`. Install Chrome
or edit the script's browser channel to match a locally installed browser.
Run from the project root:

```
python tests/browser/launch_smoke.py
```

The script starts a loopback-only server on port 5067, uses a fresh temporary
database, disables Gemini and dotenv loading, and never sends recovery email.
It tests real registration, login, CSV upload, and deterministic EDA, with
intercepted responses only for simulated outages and rate limits. It closes the
browser and test server on completion. Set AISTORA_SCREENSHOT_DIR to choose
where screenshots are written; otherwise they live in the temporary test folder.
