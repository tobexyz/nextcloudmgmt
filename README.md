# Nextcloud Working Group Management Toolkit

## Project Overview

This project provides automation tools for managing Nextcloud instances in an NGO environment, specifically designed to create and maintain working group (WG) structures with appropriate permission controls.

## Core Principles

**"Internal matters remain protected, knowledge is shared."**

The toolkit implements a permission structure that:
- Allows WGs to work autonomously with their own resources
- Maintains transparency for the broader organization
- Protects sensitive internal discussions
- Shares knowledge and public outputs

## Key Components

| Component | Purpose | Status |
|-----------|---------|--------|
| `setup_working_group.py` | Creates complete WG infrastructure (folders, calendars, collectives, talk rooms) | ✅ Active |
| `setup_folder_permissions.py` | Sets detailed ACL permissions on subfolders via WebDAV | ✅ Active |
| `check_compliance.py` | Config-driven rights compliance checker — least-privilege enforcement | ✅ Active |
| `generate_usage_report.py` | Generates HTML usage dashboard from daily serverinfo statistics | ✅ Active |
| `save_serverinfo.py` | Daily cron job — fetches Nextcloud serverinfo API and stores JSON to WebDAV | ✅ Active |
| `collectives_backup.py` | Backs up WG collectives (wikis) to prevent data loss | ✅ Active |
| `prepare.sh` | Environment setup script for dependencies | ✅ Active |

## Technology Stack

- **Language**: Python 3
- **APIs**: Nextcloud OCS API, WebDAV, CalDAV
- **Dependencies**: requests, caldav, webdavclient3, pyyaml

## Getting Started

### 1. Environment Setup

Run `./prepare.sh` to set up the virtual environment.

### 2. Configuration

Copy `.envrc.example` to `.envrc` and configure:
- `NC_URL` - Nextcloud server URL
- `NC_ANCHOR_USER` - Technical admin user
- `NC_ANCHOR_APP_PW` - App password for the anchor user
- `NC_ALL_MEMBERS_GROUP` - Group for organization-wide access
- `NC_ADMIN_GROUP` - Admin group name
- `NC_ANCHOR_GROUP` - Anchor group for administration (default: "Anchor_Group")
- `NC_QUOTA_GB` - Default quota for group folders
- `NC_PUBLIC_SUBFOLDER` - Name of public subfolder (default: "Public")
- `NC_SUBFOLDERS` - Comma-separated list of subfolders
- `NC_PUB_FOLDER_PREFIX` - Prefix for public folders (default: "01")
- `NC_PRIV_FOLDER_PREFIX` - Prefix for private folders (default: "02")

### 3. Running Scripts

#### Create Working Group
```bash
./setup_working_group.py <group_name>
```

#### Set Folder Permissions
```bash
./setup_folder_permissions.py <config.yaml>
```

## Anchor Group Concept

The `Anchor_Group` is a dedicated group for administrative access to all working group folders. Unlike the old approach where `anchor_user` was added to each working group (giving inherited permissions), the anchor group:

- Is created once and added to each working group folder
- Gets explicit full access (mask 31, permissions 31) via ACLs
- Can access all folders for administration without being a member of each working group
- Provides better security and clearer permission boundaries

**Note**: The `anchor_user` is still added to each working group as a safety net for direct access when needed.

## Test Environment

A Docker-based test environment is available in `test-setup/`:

```bash
cd test-setup
docker-compose up -d
```

The test environment includes:
- Nextcloud 33.0.2.2
- Pre-configured anchor user and groups
- Sample configuration files

To reset the test environment:
```bash
docker-compose down -v
```

## Working Group Infrastructure

### 1. File Directories (Group Folders)

The folder structure is designed to allow the WG to work autonomously while maintaining transparency for the rest of the organization.

* **Main Directory (WG-Name):**
  * **Admin Group:** Full access via group folder permissions
  * **Anchor Group:** Full access via ACL
  * **WG-Members:** Full access via group folder permissions
  * **Rest of the organization (users):** No access
* **Subfolder `01_<WG-Name>_Public`:**
  * **Read access for everyone** via share
  * **WG-Members:** Full access via ACL
* **Subfolders `02_<WG-Name>_Private` / `02_<WG-Name>_Archive`:**
  * **WG-Members:** Full access via ACL
  * **Rest of the organization:** Hidden via ACL mask 0

### 2. Knowledge & Documentation (Collectives / Wiki)

The Wiki serves as the central memory of the working group.

* **Access Type:** Public Circle (Type 2).
* **WG-Members:** Have **Contributor** status within the Circle; they can create, edit, and delete pages.
* **Rest of the organization:** Assigned the **Member** role. They can access the Collective via the app and read all content but cannot make changes. This promotes knowledge exchange within the NGO.

### 3. Schedules & Planning (Calendar)

Each WG receives its own calendar to provide transparency regarding meetings and deadlines.

* **Owner:** The `anchor_user` (technical administrator).
* **WG-Members:** Receive a share with **read-write** permissions. They can create and reschedule appointments.
* **Rest of the organization:** Receives an automatic **read-only** share. This allows everyone to see when a WG meets without interfering with the schedule.

### 4. Talk Channel

Each WG has a channel in Talk. The room is open for every registered user but initially only the WG is member of the channel.

## Technical Permission Logic (Bitmasks)

The scripts utilize Nextcloud bitmasks to strictly enforce these rules:

| Component | Target Audience | Permission (Technical) | Effect |
| :--- | :--- | :--- | :--- |
| **Group Folder** | Admin | 31 | Full Access |
| **Group Folder** | Anchor_Group | 31 | Full Access (via ACL) |
| **Group Folder** | WG-Members | 31 | Full Access |
| **Internal Folder** | Everyone (users) | 0 | Invisible |
| **Internal Folder** | WG-Members | 31 | Full Access |
| **Public Subfolder** | Everyone (users) | 1 | Read Only (via share) |
| **Public Subfolder** | WG-Members | 31 | Full Access (via ACL) |
| **Calendar** | WG-Group | read-write | Editing enabled |
| **Calendar** | Everyone (users) | read | View appointments only |
| **Collective** | Circle-Member (WG-Group) | Moderator | Edit Wiki |
| **Collective** | Circle-Member (Group All) | Member | Read  |
| **Talk WG-Group channel** | WG-Member | isMember | joinable NGO wide |
| **Talk WG-Group channel** | Everyone (users) | can join if wanted | joinable NGO wide |

## Configuration Files

### sample-permissions.yaml

Example configuration for `setup_folder_permissions.py`:

```yaml
global_groups:
 - all_users
 - GroupC
 - GroupC_Lead
 - GroupC_Subgroup1

groupfolders:
 - name: "GroupC"
   folders:
   - path: "01_GroupC_Public"
     block: []
     read: ["all_users"]
     write: ["GroupC", "Anchor_Group"]
   - path: "02_GroupC_Private"
     block: []
     read: ["GroupC"]
     write: ["GroupC","Anchor_Group"]
```

## Rights Compliance Checker

The `check_compliance.py` script implements a least-privilege compliance process. It compares the actual Nextcloud permission state against the expected state derived from:

1. **Implicit WG model** (from `setup_working_group.py`) — base permissions every WG should have
2. **YAML config files** (same format as `setup_folder_permissions.py`) — fine-grained overrides

The script automatically discovers **all group folders** on the server and checks them against the implicit model. YAML configs add detailed subfolder ACL checks for the groups they cover.

### What it checks

| Category | Checks |
|----------|--------|
| Group Folder Membership | Correct groups with correct permissions; admin group absent |
| Root ACL | WG group denied on root (forces subfolder access) |
| Subfolder ACLs | Expected ACLs present; no excess group permissions |
| User ACLs | Direct user permissions flagged for review |
| Shares | Correct share types and permissions per subfolder |
| User Shares | Direct user shares flagged for review |
| Public Links | No password → violation; no expiry → warning |
| Calendar Shares | WG group writable; all_users read-only |
| Collective Membership | Skipped gracefully if app is disabled |

### YAML Config Format

Same format as `setup_folder_permissions.py`. Supports a `group` override for cases where the internal group ID differs from the folder name:

```yaml
groupfolders:
  - name: "Infrastruktur"
    group: "BUNDLVHH-IT"  # optional: actual group ID if renamed
    folders:
      - path: "01_Infra_Public"
        block: []
        read: ["all_users"]
        write: ["BUNDLVHH-IT"]
```

### Usage

```bash
# Check against one config file
python3 check_compliance.py permissions.yaml

# Check against multiple config files
python3 check_compliance.py configs/*.yaml

# Check against a directory of configs
python3 check_compliance.py --config-dir configs/

# Preview expected state without contacting the server
python3 check_compliance.py --dry-run permissions.yaml

# Upload report to Nextcloud after generation
python3 check_compliance.py permissions.yaml --upload
```

### Output

- **Markdown report** — saved to `reports/` and optionally uploaded to Nextcloud (viewable in Text app)
- **JSON report** — saved to `reports/` for machine processing
- **Exit code** — 0 if no violations, 1 if violations found (useful for cron alerting)

### Finding Types

| Status | Meaning | Severity |
|--------|---------|----------|
| OK | Actual matches expected | — |
| DRIFT | Permission exists but differs from expected | violation (more permissive) or warning (less) |
| MISSING | Expected permission not found on server | warning |
| EXCESS | Permission exists but not in any config | violation (write/full) or warning (read) |

### Periodic Execution

Add to cron for continuous least-privilege enforcement:

```bash
# Daily compliance check at 7:00 AM
0 7 * * * cd /path/to/project && source venv/bin/activate && python3 check_compliance.py configs/*.yaml --upload
```

## Usage Statistics Dashboard

The `save_serverinfo.py` script fetches Nextcloud server statistics daily via the serverinfo API and stores them as JSON files on the server via WebDAV. The `generate_usage_report.py` script processes these files into a visual HTML dashboard.

### Data Collection

`save_serverinfo.py` runs daily via cron and stores files as `stats_raw_YYYY-MM-DD_HH-MM-SS.json` in the configured `NC_STATS_DIR`.

### Report Generation

`generate_usage_report.py` downloads all stored stats files, extracts metrics, and generates a self-contained HTML page with Chart.js charts:

- **Users Over Time** — active users (24h) and total registered users
- **Storage Growth** — total files and database size
- **Free Disk Space** — remaining disk space trend

### Usage

```bash
# Generate and upload the usage report
python3 generate_usage_report.py
```

The report is uploaded as `usage_report.html` to the parent folder of `NC_STATS_DIR`.

### Periodic Execution

```bash
# Daily stats collection at 6:00 AM
0 6 * * * cd /path/to/project && source venv/bin/activate && python3 save_serverinfo.py

# Generate report at 6:05 AM (after stats are collected)
5 6 * * * cd /path/to/project && source venv/bin/activate && python3 generate_usage_report.py
```

### Environment Variables

Both scripts use the same env vars: `NC_URL`, `NC_ANCHOR_USER`, `NC_ANCHOR_APP_PW`, `NC_STATS_DIR`.

## Documentation

- `README.md` - This file
- `sample-permissions.yaml` - Example configuration
- `test-setup/README-TEST.md` - Test environment documentation
