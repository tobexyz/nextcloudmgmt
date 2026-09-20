# Containerized for the dev-01/docker_apps move (ansible-stack-redesign
# spec, Group 2b.4 consumer contracts), same pattern as bfs_odl/smarthome/
# nextcloud-maintanance. Only `save_serverinfo.py` -- the sole script the
# real cron (processBUND_backup.sh) invokes; collectives_backup.py is
# commented out there and every other script in this repo (setup_working_
# group.py, setup_folder_permissions.py, check_compliance.py,
# generate_usage_report.py) is a manually-run admin tool, out of scope for
# this scheduled job.
#
# Pinned by digest -- tag noted for an audit trail. Verified 2026-09-20:
# `3.13.14-slim-bookworm` was last pushed 2026-08-05 (46 days old), clears
# this project's 7-day dependency-quarantine rule (the plain `3.13-slim-
# bookworm`/`3.13.15-slim-bookworm` tags were pushed yesterday, too fresh).
FROM python:3.13.14-slim-bookworm@sha256:de572b33eae61a53675a87bbd02b5e365df7b6b2b06c9276124e965cec08c452
ENV PYTHONUNBUFFERED=1
RUN useradd -m -d /app app
WORKDIR /app
COPY requirements-serverinfo.txt ./
RUN pip install --no-cache-dir --no-input -r requirements-serverinfo.txt
COPY --chown=app:app save_serverinfo.py ./
RUN chown app:app /app
USER app

# mode: scheduled in docker_apps -- run to completion once per invocation
# (`docker compose run --rm`), not a long-lived daemon.
ENTRYPOINT ["python3", "save_serverinfo.py"]
