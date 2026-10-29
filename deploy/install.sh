#!/bin/sh
# Builds a Plain bot and installs it as a systemd service on Ubuntu or Debian.
# Usage: sudo deploy/install.sh examples/bots/showcase.eng
set -e
if [ "$(id -u)" != 0 ] || [ -z "$1" ]; then
    echo "Usage: sudo deploy/install.sh path/to/bot.eng" >&2
    exit 1
fi
here=$(cd "$(dirname "$0")/.." && pwd)
apt-get install -y clang lld libcurl4-openssl-dev libgc-dev python3
if command -v plain >/dev/null 2>&1; then
    plain build "$1" -o /tmp/plain-bot
elif command -v cargo >/dev/null 2>&1; then
    cargo install --quiet --path "$here/compiler-rs" --root /usr/local
    plain build "$1" -o /tmp/plain-bot
else
    python3 "$here/engc.py" build "$1" -o /tmp/plain-bot
fi
id plain >/dev/null 2>&1 || useradd --system --home-dir /opt/plain-bot --shell /usr/sbin/nologin plain
mkdir -p /opt/plain-bot
install -m 755 /tmp/plain-bot /opt/plain-bot/bot
if [ ! -f /opt/plain-bot/bot.env ]; then
    printf "Telegram bot token (from @BotFather): "
    read -r token
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$token" > /opt/plain-bot/bot.env
    chmod 600 /opt/plain-bot/bot.env
fi
chown -R plain:plain /opt/plain-bot
install -m 644 "$here/deploy/plain-bot.service" /etc/systemd/system/plain-bot.service
systemctl daemon-reload
systemctl enable --now plain-bot
systemctl restart plain-bot
echo "The bot is running. Logs: journalctl -u plain-bot -f"
