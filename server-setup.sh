#!/usr/bin/env bash
#
# server-setup.sh — Hetzner CX22 Ubuntu VPS setup for GarminBot
#
# Usage: ssh root@your-server 'bash -s' < server-setup.sh
#    or: scp server-setup.sh root@server: && ssh root@server bash server-setup.sh
#
# Idempotent: safe to re-run.

set -euo pipefail

BOTUSER="garminbot"
TIMEZONE="Europe/Lisbon"

echo "=== GarminBot Server Setup ==="
echo ""

# ---- 1. System updates ----
echo "[1/6] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ---- 2. Create dedicated user ----
echo "[2/6] Creating user '$BOTUSER'..."
if id "$BOTUSER" &>/dev/null; then
    echo "  User '$BOTUSER' already exists, skipping."
else
    adduser --disabled-password --gecos "GarminBot Service" "$BOTUSER"
    echo "  User '$BOTUSER' created."
    echo "  Setting password for '$BOTUSER' (needed for sudo):"
    passwd "$BOTUSER"
fi

# Ensure user is in sudo group
if groups "$BOTUSER" | grep -q sudo; then
    echo "  User '$BOTUSER' already in sudo group."
else
    usermod -aG sudo "$BOTUSER"
    echo "  Added '$BOTUSER' to sudo group."
fi

# ---- 3. Install Docker ----
echo "[3/6] Installing Docker..."
if command -v docker &>/dev/null; then
    echo "  Docker already installed: $(docker --version)"
else
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    echo "  Docker installed: $(docker --version)"
fi

# Add bot user to docker group
if groups "$BOTUSER" | grep -q docker; then
    echo "  User '$BOTUSER' already in docker group."
else
    usermod -aG docker "$BOTUSER"
    echo "  Added '$BOTUSER' to docker group."
fi

# ---- 4. Set timezone ----
echo "[4/6] Setting timezone to $TIMEZONE..."
timedatectl set-timezone "$TIMEZONE"
echo "  Timezone: $(timedatectl show --property=Timezone --value)"

# ---- 5. Configure UFW firewall ----
echo "[5/6] Configuring UFW firewall..."
apt-get install -y -qq ufw
ufw default deny incoming 2>/dev/null || true
ufw default allow outgoing 2>/dev/null || true
ufw allow OpenSSH 2>/dev/null || true
echo "y" | ufw enable 2>/dev/null || true
echo "  UFW status:"
ufw status

# ---- 6. Harden SSH ----
echo "[6/6] Hardening SSH..."
echo ""
echo "  ============================================================"
echo "  WARNING: This step will DISABLE password authentication."
echo "  You MUST have an SSH key configured BEFORE proceeding."
echo "  ============================================================"
echo ""

# Check if any authorized_keys exist
ROOT_KEYS="/root/.ssh/authorized_keys"
BOT_KEYS="/home/$BOTUSER/.ssh/authorized_keys"

HAS_KEYS=false
if [ -f "$ROOT_KEYS" ] && [ -s "$ROOT_KEYS" ]; then
    HAS_KEYS=true
    echo "  Found SSH keys in $ROOT_KEYS"
fi
if [ -f "$BOT_KEYS" ] && [ -s "$BOT_KEYS" ]; then
    HAS_KEYS=true
    echo "  Found SSH keys in $BOT_KEYS"
fi

if [ "$HAS_KEYS" = false ]; then
    echo ""
    echo "  No SSH keys found. Please paste your public SSH key now."
    echo "  (The key starts with 'ssh-rsa', 'ssh-ed25519', or similar)"
    echo ""
    read -r -p "  SSH public key: " SSH_KEY
    if [ -z "$SSH_KEY" ]; then
        echo "  ERROR: No key provided. Skipping SSH hardening."
        echo "  Re-run this script after adding your SSH key."
        echo ""
        echo "=== Setup complete (SSH hardening skipped) ==="
        exit 0
    fi
    # Install key for both root and botuser
    mkdir -p /root/.ssh
    echo "$SSH_KEY" >> /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys

    mkdir -p "/home/$BOTUSER/.ssh"
    echo "$SSH_KEY" >> "/home/$BOTUSER/.ssh/authorized_keys"
    chown -R "$BOTUSER:$BOTUSER" "/home/$BOTUSER/.ssh"
    chmod 700 "/home/$BOTUSER/.ssh"
    chmod 600 "/home/$BOTUSER/.ssh/authorized_keys"
    echo "  SSH key installed for root and $BOTUSER."
fi

echo ""
read -r -p "  Disable password auth and root login now? (y/N): " CONFIRM
if [[ "$CONFIRM" =~ ^[Yy]$ ]]; then
    cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak

    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
    sed -i 's/^#\?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config

    if systemctl list-units --type=service | grep -q 'ssh\.service'; then
        systemctl restart ssh
    else
        systemctl restart sshd
    fi
    echo "  SSH hardened: password auth disabled, root login disabled."
    echo ""
    echo "  IMPORTANT: Test SSH key login in a NEW terminal before closing this session!"
else
    echo "  SSH hardening skipped. You can re-run this script later."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Log in as $BOTUSER:  ssh $BOTUSER@$(hostname -I | awk '{print $1}')"
echo "  2. Clone the repo:      git clone <repo-url> ~/GarminBot"
echo "  3. Create .env:         cd ~/GarminBot && cp .env.example .env && nano .env"
echo "  4. Deploy:              bash deploy.sh"
