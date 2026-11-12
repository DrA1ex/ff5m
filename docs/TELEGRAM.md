# Telegram Bot Setup

The Telegram bot lets you control the printer remotely and create timelapse videos. If you only need timelapses, use Forge-X's built-in [`timelapse` setting](CAMERA.md#timelapse).

The printer has too few resources to run the bot itself, so run it on an external server. The server connects to the printer over the local network or through an SSH tunnel that the printer opens to the server.

## Bot Registration

1. Open [BotFather](https://t.me/BotFather) in Telegram.
2. Send the `/newbot` command.
3. Choose a name for your bot.
4. Choose a username ending with `_bot` (for example, `ff5m_super_bot`).
5. Save the bot token (a long string) that BotFather returns.

## Connecting the server to the printer

### Local network

If the printer and the server are on the same network, no SSH tunnel is needed. Configure the bot in `telegram.conf`; download it from the printer or from the repository: [telegram.conf](/telegram/telegram.conf).

### Remote server through an SSH tunnel

The printer generates its own SSH key pair. Its public key is in Fluidd under **Configuration -> mod_data -> ssh.pub.txt**; add its contents to `~/.ssh/authorized_keys` of the user the printer logs in as on your server.

Set the server details in Fluidd under **Configuration -> mod_data -> ssh.conf**:

```cfg
# Remote SSH server
SSH_SERVER=your_server_host

# SSH server port (default is 22)
SSH_PORT=22

# Username for authorization
SSH_USER=user

# Video streaming port on the remote server (default is 8080)
VIDEO_PORT=8080

# Moonraker port on the remote server (default is 7125)
MOON_PORT=7125

# Command to run on the remote server when the tunnel is established (e.g., docker restart <container_id>)
REMOTE_RUN=""
```

The tunnel forwards the printer's camera (port `8080`) and Moonraker (port `7125`) to `VIDEO_PORT` and `MOON_PORT` on the server. Then enable it:

```gcode
SET_MOD PARAM="zssh" VALUE=1
```

The mod manages the tunnel and restarts it if needed. Set `REMOTE_RUN` to restart the bot container whenever the tunnel is established, for example `REMOTE_RUN="docker restart <container_id>"`. Run `docker ps` on the server to find the container ID.

## Server setup

### Automatic setup

The installation script is written for Ubuntu and Debian and does not work on other distributions. It installs Docker, sets up the bot, and creates the `ff5m` user.

```bash
sudo su
apt update && apt install wget -y  # Ensure wget is installed
bash <(wget --cache=off -q -O - https://raw.githubusercontent.com/DrA1ex/ff5m/refs/heads/main/telegram/telegram.sh)
```

For a remote server, add the printer's SSH public key to the `ff5m` user afterward, as described in [step 9](#9-configure-ssh-access).

### Manual setup

##### 1. Install Requirements
Install Docker by following the [official documentation](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository).

##### 2. Create Directory for Bot
```bash
mkdir -p /opt/telegram-moonraker-bot
cd /opt/telegram-moonraker-bot
```

##### 3. Create Required Additional Directories
```bash
mkdir config log timelapse_finished timelapse
chmod 777 config log timelapse_finished timelapse
```

##### 4. Download `docker-compose.yml`
```bash
wget --cache=off -q -O docker-compose.yml https://raw.githubusercontent.com/dra1ex/ff5m/refs/heads/main/telegram/docker-compose.yml
```
Review `docker-compose.yml` and adjust it if needed.

##### 5. Download `telegram.conf`
```bash
wget --cache=off -q -O config/telegram.conf https://github.com/dra1ex/ff5m/raw/refs/heads/main/telegram/telegram.conf
```
Review `telegram.conf` and adjust it if needed.

##### 6. Set Up Container and Run
```bash
docker compose up -d
```

Steps 7 to 9 are needed only for the SSH tunnel.

##### 7. Create a New User
```bash
useradd -m -s /bin/bash -G docker ff5m
```

##### 8. Switch to the New User
```bash
su - ff5m
```

##### 9. Configure SSH Access
```bash
mkdir -p .ssh
cat > .ssh/authorized_keys
```

Paste the printer's public key (`ssh.pub.txt`) and press `Ctrl + D` to save.

## Telegram bot configuration

For the bot's own options (custom commands, notifications, and timelapse settings), see the [moonraker-telegram-bot repository](https://github.com/nlef/moonraker-telegram-bot).
