# Deploying bottski to a droplet

One small box runs everything. ~10 minutes start to finish.

## 1. Create the droplet
DigitalOcean → Create → Droplet:
- Ubuntu 24.04 LTS, Basic, **$6/mo (1 vCPU / 1GB)**
- Region: any EU region is fine (all timestamps are UTC)
- Authentication: **SSH key** (add your Mac's `~/.ssh/id_ed25519.pub`)

## 2. Provision
```
ssh root@<droplet-ip>
curl -fsSL https://raw.githubusercontent.com/yoyomaaah/bottski/main/deploy/provision.sh | bash
```
Installs git/uv, creates the `bottski` user, clones the repo, syncs deps,
installs the systemd timer (collect every 20 min, UTC). Re-run any time to update.

## 3. Secrets
From your Mac (never commit .env):
```
scp .env root@<droplet-ip>:/home/bottski/bottski/.env
ssh root@<droplet-ip> 'chown bottski:bottski /home/bottski/bottski/.env && chmod 600 /home/bottski/bottski/.env'
```
Optional alerting: create a healthchecks.io project, copy its ping key, add to .env:
`HEALTHCHECKS_PING_BASE=https://hc-ping.com/<ping-key>` — checks auto-create on
first ping; set each check's schedule to every 20 min with a 10 min grace.

## 4. Seed with existing data (optional but recommended)
Carry over what your Mac already collected:
```
scp data/bottski.db root@<droplet-ip>:/home/bottski/bottski/data/bottski.db
ssh root@<droplet-ip> 'chown -R bottski:bottski /home/bottski/bottski/data'
```
(Do this BEFORE the first timer run, or stop the timer first; don't overwrite a
db that has newer data than your Mac's copy.)

## 5. Verify
```
ssh root@<droplet-ip>
systemctl start bottski-collect.service        # run once now
journalctl -u bottski-collect.service -n 30    # see the log
sudo -u bottski bash -c 'cd ~/bottski && ~/.local/bin/uv run bottski status'
systemctl list-timers 'bottski-*'              # next scheduled run
```

## Updating
```
ssh root@<droplet-ip> 'curl -fsSL https://raw.githubusercontent.com/yoyomaaah/bottski/main/deploy/provision.sh | bash'
```
