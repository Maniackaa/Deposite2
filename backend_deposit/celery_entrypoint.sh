#!/bin/sh

# run a worker :)
celery -A backend_deposit worker --loglevel=info --concurrency 1 -E