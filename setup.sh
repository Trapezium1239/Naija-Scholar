#!/bin/bash
echo "==================================================="
echo "NAIJA SCHOLAR ENGINE - LIGHTHOUSE INTEL ACADEMY"
echo "==================================================="
echo "Cleaning up port 8000..."
lsof -ti:8000 | xargs kill -9 2>/dev/null

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt

python3 test_all.py
if [ $? -ne 0 ]; then
    echo "Tests failed. Halting."
    exit 1
fi

echo "Booting architecture in background..."
SEED_ENABLED=false python3 main.py &
python3 autonomous_seeder.py &
wait