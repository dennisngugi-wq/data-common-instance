# Pipeline image: fetch, validate, publish, merge. Built by docker compose (service "pipeline").
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /project
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The project folder (config/, automation/src/, data/) is mounted at /project at run time, so feed and code
# changes take effect without rebuilding.
CMD ["python", "automation/src/run_pipeline.py"]
