"""Locust entrypoint for the frontend / login-wave test in DISTRIBUTED mode.

A single Locust process is GIL-bound to one core and can't drive a 2,500-browser
stampede — it saturates the generator, not the UI. Run this with `--processes N` so
Locust forks N worker processes across the D16's cores and aggregates their stats.

Run (in the loadgen pod, from /tmp/loadtest):
  python3 -m locust -f fe_locustfile.py --headless \
    -u 2500 -r 42 -t 6m --processes 12 \
    --host https://hackathon.evolution.ml

Watch UI-pod CPU alongside it (separate terminal):
  watch kubectl -n neuro-san-hackathon top pods -l app=ui-node

Only FrontendUser is imported here, so Locust runs the UI-tier load only (not the
backend design users in users.py).
"""

from users import FrontendUser  # noqa: F401  — Locust auto-discovers this User class
