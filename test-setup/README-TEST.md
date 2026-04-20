# Local Test Environment Setup

This document describes how to set up a local Nextcloud test environment using Docker Compose.

## Prerequisites

- Docker and Docker Compose installed
- `uv` package manager (for running Python scripts)

## Quick Start

1. **Start the test environment:**
   ```bash
   cd test-setup
   docker-compose up -d
   ```

2. **Run the setup script:**
   ```bash
   ./setup.sh
   ```

3. **Load test environment variables:**
   ```bash
   source test-environment
   ```

4. **Test the setup:**
   ```bash
   python ../setup_working_group.py
   ```

## Environment Details

- **Nextcloud URL:** http://localhost:8080
- **Admin credentials:** admin / admin123
- **Anchor user:** anchor_user / anchoruser123

## Available Apps

The setup script enables:
- groupfolders
- circles
- collectives
- spreed (Talk)

## Stopping the Environment

```bash
docker-compose down
```

To also remove volumes (data):
```bash
docker-compose down -v
```

## Troubleshooting

### Nextcloud not responding
Wait a few more minutes - Nextcloud needs time to initialize.

### Connection refused
Check if Docker containers are running:
```bash
docker-compose ps
```

### View logs
```bash
docker-compose logs -f
```

## Running prepare.sh from test-setup

To run the main prepare script from inside the test-setup directory:

```bash
cd test-setup
../prepare.sh
```

Or run it directly:

```bash
./prepare.sh
```

This will set up the virtual environment and can be used to test the scripts against the local Nextcloud instance.
