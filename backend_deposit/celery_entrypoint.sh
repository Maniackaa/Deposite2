#!/bin/sh

#cp celery.service /etc/systemd/system/
#systemctl start celery.service
celery -A backend_deposit worker -l warning -n myworker1  --concurrency=4
#celery multi start 1 -A backend_deposit worker -l INFO -n myworker1  --concurrency=4 --pidfile=/var/run/celery/%n.pid
#celery multi restart 1 --pidfile=/var/run/celery/%n.pid