
source .venv/bin/activate && python -u poller.py

if [ "$(date +%u)" = "1" ] && [ "$(date +%H)" = "00" ] && [ "$(date +%M)" -lt 15 ]; then
    source .venv/bin/activate && python -u poller.py --purge
fi
