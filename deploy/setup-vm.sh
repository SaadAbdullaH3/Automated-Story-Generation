#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu server for the app, run from the cloned repo:
#
#   git clone https://github.com/SaadAbdullaH3/Automated-Story-Generation.git storygen
#   cd storygen && bash deploy/setup-vm.sh
#
# Safe to run again: every step checks before it changes anything.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n== %s\n' "$*"; }

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
if getent group docker >/dev/null; then
  sudo usermod -aG docker "$USER"      # docker without sudo, from the next login
fi
docker_cmd="docker"
if ! docker info >/dev/null 2>&1; then docker_cmd="sudo docker"; fi
$docker_cmd compose version

say "Firewall: ports 80 and 443"
# Oracle's Ubuntu images reject everything but SSH in iptables, on top of the
# cloud's own security list: both must allow the ports, or HTTPS never arrives.
reject_line=$(sudo iptables -L INPUT -n --line-numbers | awk '$2 == "REJECT" {print $1; exit}')
if [ -n "${reject_line}" ]; then
  for rule in "tcp 80" "tcp 443" "udp 443"; do
    set -- $rule
    if ! sudo iptables -C INPUT -p "$1" --dport "$2" -j ACCEPT 2>/dev/null; then
      sudo iptables -I INPUT "${reject_line}" -p "$1" --dport "$2" -j ACCEPT
      echo "opened $1/$2"
    fi
  done
  if command -v netfilter-persistent >/dev/null 2>&1; then
    sudo netfilter-persistent save
  fi
else
  echo "no REJECT rule in iptables — nothing to open here"
fi

say "Data directory"
# The containers run as uid 10001, not as you: give them the folder, or the
# first film fails with "permission denied" on a Linux host.
sudo install -d -o 10001 -g 10001 data

say ".env"
if [ ! -f .env ]; then
  cp .env.example .env
  domain=${DOMAIN:-}
  if [ -z "${domain}" ]; then
    # No name of your own: <ip>.sslip.io resolves to this machine for free,
    # no account needed, and Caddy can get a certificate for it.
    ip=$(curl -fsS https://api.ipify.org || true)
    domain="${ip//./-}.sslip.io"
  fi
  {
    echo ""
    echo "# --- this server ---"
    echo "DOMAIN=${domain}"
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
    echo "COOKIE_SECURE=1"
  } >> .env
  chmod 600 .env
  echo "wrote .env with DOMAIN=${domain} and a generated database password"
else
  echo ".env exists — left as it is"
fi

say "Next"
cat <<'EOF'
1. Put your API keys in .env (nano .env) — the same ones as on your laptop.
   For GitHub sign-in, register a second OAuth app whose callback is
   https://<DOMAIN>/api/auth/github/callback and use its id and secret here.
2. Start everything:
     docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
   (log out and back in first if `docker` says permission denied — the
   group change above takes effect at the next login).
3. Open https://<DOMAIN> — the first account you make is the admin.
EOF
