# GitHub Actions Configuration

## Repository Variables

The deployment workflow uses GitHub repository variables for configuration.

### Setting Up ENABLE_RADIO_LOGS

To enable disk logging in your deployed containers:

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click the **Variables** tab
4. Click **New repository variable**
5. Set:
   - **Name**: `ENABLE_RADIO_LOGS`
   - **Value**: `true` (or `false` to disable)
6. Click **Add variable**

### How It Works

The GitHub Actions workflow:
1. Reads the `ENABLE_RADIO_LOGS` repository variable (defaults to `false` if not set)
2. Passes it as an environment variable to the SSH deployment step
3. Sets it in the Docker Compose command on the remote server
4. Docker Compose then passes it to the containers via `docker-compose.yaml`

### Workflow Flow

```
GitHub Repository Variable (ENABLE_RADIO_LOGS)
         ↓
GitHub Actions Workflow (.github/workflows/deploy.yaml)
         ↓
SSH to Remote Server
         ↓
Docker Compose Environment Variable
         ↓
Container Environment Variable
         ↓
Application Logging Configuration
```

### Testing Changes

After setting the repository variable:
1. Push to the `main` branch
2. GitHub Actions will automatically deploy with the new setting
3. Check logs: `docker logs radio-capture` to verify logging configuration
4. If enabled, verify log files exist: `ls -lh /home/mindlin/radio_capture/data/logs/`

### Other Environment Variables

The workflow also uses:
- **Secrets**:
  - `OPENAI_API_KEY` - OpenAI API key for transcription
  - `SSH_HOST` - Deployment server hostname
  - `SSH_PORT` - SSH port
  - `SSH_USER` - SSH username
  - `SEHEL_SSH_KEY` - SSH private key

- **Repository Variables** (optional):
  - `ENABLE_RADIO_LOGS` - Enable disk logging (true/false)

### Manual Override

You can override the variable on the server by setting it in a `.env` file:
```bash
cd /home/mindlin/radio_capture
echo "ENABLE_RADIO_LOGS=true" >> .env
docker compose up -d
```

This will take precedence over the GitHub Actions variable for local testing.
