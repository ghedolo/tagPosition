
scp update.sh harden_pi.sh auth.py map.py poller.py server.py show.py pi@<YOUR_PI_IP>:/home/pi/tagPosition
scp patches/*.py pi@<YOUR_PI_IP>:/home/pi/tagPosition/patches/
echo "remember: sudo systemctl restart tagmap"
