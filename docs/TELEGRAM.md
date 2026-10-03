# Telegram Bot Setup

The Flashforge AD5M (Pro) has limited hardware resources, so `moonraker-telegram-bot` cannot practically run on the printer itself.
Instead, the bot can be hosted on an external server that the printer can access via SSH. 

## Bot Registration
To register your Telegram bot:

1. Visit [BotFather](https://t.me/BotFather) on Telegram.
2. Use the `/newbot` command to create a new bot.
3. Choose a name for your bot.
4. Set a username ending with _bot (e.g., ff5m_super_bot).
5. Receive a bot token (a long string) and save it for later use.

## SSH-tunnel setup

### Local Network Setup

If the printer and server are on the same network, SSH is not required. Instead, configure the bot using the telegram.conf file.
You can download this file from the printer or from the repository: [telegram.conf](/telegram/telegram.conf).

### Remote Host Setup

The mod automatically generates SSH keys for passwordless authentication. These keys are located at: **Configuration -> mod_data -> ssh.pub.txt**.  
This is a public key, you need to add its contents to the `~/.ssh/authorized_keys file` on your server.

#### Printer configuration

To configure the printer for SSH access, edit the configuration file located in Fluidd under: **Configuration -> mod_data -> ssh.conf**

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

## Host configuration

#### Automatic Setup

Note that the installation script is written for Ubuntu/Debian and won't work with non-Debian distributions.

```bash
sudo su
apt update && apt install wget -y  # Ensure wget is installed
bash <(wget --cache=off -q -O - https://raw.githubusercontent.com/DrA1ex/ff5m/refs/heads/main/telegram/telegram.sh)
```

**For remote network**: After running the script, you will need to manually add the SSH public key to the server.

#### Manual setup

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
- **Note:** Review `docker-compose.yml` and apply any necessary changes.

##### 5. Download `telegram.conf`
```bash
wget --cache=off -q -O config/telegram.conf https://github.com/dra1ex/ff5m/raw/refs/heads/main/telegram/telegram.conf
```
- **Note:** Review `telegram.conf` and apply any necessary changes.

##### 6. Set Up Container and Run
```bash
docker compose up -d
```

##### 7. Create a New User
```bash
useradd ff5m
usermod -a -G docker ff5m
```

##### 8. Switch to the New User
```bash
su - ff5m
```

##### 9. Configure SSH Access
- Create the `.ssh` directory:
  ```bash
  mkdir .ssh
  ```

- Add your SSH public key to `authorized_keys`:
  ```bash
  cat > .ssh/authorized_keys
  ```

  (Paste the contents of your public key file and press `Ctrl + D` to save.)

## Printer configuration

To enable SSH tunneling, modify the mod parameter:

```
SET_MOD PARAM="zssh" VALUE=1
```

The mod will automatically manage the SSH tunnel and restart it if needed. It is recommended to set the `REMOTE_RUN` parameter in `ssh.conf` to restart the bot container when the SSH tunnel is established.  

For example:
```
REMOTE_RUN="docker restart <container_id>"
```

You can obtain the container ID by running `docker ps` on the server.

## Telegram bot configuration

For additional configuration options and advanced settings for the Telegram bot, refer to the official moonraker-telegram-bot [GitHub repository](https://github.com/nlef/moonraker-telegram-bot)

This repository provides detailed documentation on configuring the bot, including custom commands, notifications, and timelapse settings.
