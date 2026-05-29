# Deploying to a Hostinger VPS

End-state: the bot lives on your VPS, runs every Sunday via cron-in-Docker,
emails you the digest, syncs Notion, and publishes the latest run + a full
archive at `https://<your-vps-hostname>/`. Pushing to `main` on GitHub
auto-deploys the new code.

## One-time VPS setup

SSH into the VPS as root (Hostinger gives you these creds in the panel).

```bash
# 1. Install Docker (skip if Hostinger's image already has it)
curl -fsSL https://get.docker.com | sh

# 2. Clone the repo
mkdir -p /root/warren-bot && cd /root/warren-bot
git clone https://github.com/<you>/warren-buffet-bot.git .

# 3. Create the env file
cp .env.example .env
nano .env          # fill in real values
chmod 600 .env

# 4. First build + start
docker compose up -d --build

# 5. Verify
docker compose ps                  # both services should be "running"
docker compose logs --tail=50 bot  # should show "tail -F /var/log/warren.log" running
curl -I http://localhost           # Caddy should answer
```

Hit `https://<your SITE_HOSTNAME>` in a browser — you'll see the landing page.
There won't be a `preview.html` yet until either cron fires or you trigger a
manual run (next section).

## Triggering a manual run

```bash
docker compose exec bot warren-bot run
```

That runs the bot inside the container exactly as cron would, including writing
`out/preview.html`, archiving it, sending the email, and updating Notion. You
can also pass `--limit 50` to do a fast smoke test.

## Where things live

| What                           | Where                                              |
|--------------------------------|----------------------------------------------------|
| Bot logs                       | `docker compose logs bot` (tails `/var/log/warren.log`) |
| Latest HTML digest             | `out/preview.html` on the host                     |
| Archived runs                  | `out/archive/YYYY-MM-DD.html`                      |
| yfinance cache                 | `.cache/` on the host (survives rebuilds)          |
| Caddy TLS certs                | `caddy_data` Docker volume (don't delete it)       |

## Auto-deploy from GitHub

`.github/workflows/deploy.yml` SSHes into the VPS on every push to `main` and
runs `git pull && docker compose up -d --build`. You need to give it SSH access:

### Generate a deploy key (do this on your laptop)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/warren-vps-deploy -N "" -C "github-actions-deploy"
```

That makes two files:
- `~/.ssh/warren-vps-deploy`     — private key (goes into GitHub secret)
- `~/.ssh/warren-vps-deploy.pub` — public key (goes onto the VPS)

### Install the public key on the VPS

```bash
# On the VPS:
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<paste contents of warren-vps-deploy.pub here>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### Add GitHub secrets

Repo → Settings → Secrets and variables → Actions → New repository secret.
Create all four:

| Secret name   | Value                                                     |
|---------------|-----------------------------------------------------------|
| `VPS_HOST`    | Your VPS hostname or IP                                   |
| `VPS_USER`    | `root` (or whatever user owns the repo dir)               |
| `VPS_SSH_KEY` | Full contents of `~/.ssh/warren-vps-deploy` (private key) |
| `VPS_PATH`    | `/root/warren-bot` (or wherever you cloned it)            |

### Test it

```bash
git commit --allow-empty -m "test deploy"
git push
```

Watch the run in the Actions tab. It should finish in under a minute. After it
succeeds, `docker compose ps` on the VPS should show fresh "Created" timestamps.

## Updating things

- **Code changes** — just `git push`. Deploy workflow rebuilds the container.
- **Secrets / `.env` changes** — edit `.env` on the VPS, then
  `docker compose up -d` to recreate containers with the new env.
- **Schedule changes** — edit `deploy/crontab` locally, push to main. After
  redeploy, cron picks up the new schedule on next container start.
- **Time zone** — change `TZ` in `.env`, then `docker compose up -d`.

## Troubleshooting

**Email didn't arrive Sunday.** Check `docker compose logs bot` for the run
output. If you see "Authentication failed" or `GMAIL_APP_PASSWORD` errors, the
cron-vs-env gotcha is firing — verify `/etc/environment` exists in the
container (`docker compose exec bot cat /etc/environment`). It should have all
your `.env` vars in it. If it's empty, the entrypoint isn't running properly.

**HTTPS not working.** Caddy needs the public hostname to resolve to the VPS
over the open internet, and ports 80 + 443 need to be reachable. Hostinger's
firewall usually allows these by default; check the VPS panel if not.
`docker compose logs caddy` will show what Let's Encrypt is complaining about.

**Site shows a 404 for /preview.html.** The first cron run hasn't happened yet.
Run `docker compose exec bot warren-bot run` once to generate it.

**Want to stop the GitHub Actions backup workflow entirely.** Delete
`.github/workflows/weekly.yml`. It's currently manual-trigger-only, so it
won't fire on its own.
