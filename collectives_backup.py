import os
import requests
import shutil
import sys
from datetime import datetime
from urllib.parse import unquote

# --- CONFIGURATION ---
NEXTCLOUD_URL = os.getenv("NC_URL")
ANCHOR_USER = os.getenv("NC_ANCHOR_USER")
ANCHOR_APP_PW = os.getenv("NC_ANCHOR_APP_PW")
NC_COLLECTIVES_FOLDER = os.getenv("NC_COLLECTIVES_FOLDER")
NC_COLLECTIVES_BACKUP_FOLDER = os.getenv("NC_COLLECTIVES_BACKUP_FOLDER")
NC_COLLECTIVES_BACKUP_COUNT = os.getenv("NC_COLLECTIVES_BACKUP_COUNT")



if not all([NEXTCLOUD_URL, ANCHOR_USER, ANCHOR_APP_PW, NC_COLLECTIVES_FOLDER, NC_COLLECTIVES_BACKUP_FOLDER]):
    print("Error: Missing required environment variables (NC_URL, NC_ANCHOR_USER, NC_ANCHOR_APP_PW, NC_COLLECTIVES_FOLDER, NC_COLLECTIVES_BACKUP_FOLDER)")
    sys.exit(1)

# Source folder to download from

REMOTE_SOURCE_PATH = f"/remote.php/dav/files/{ANCHOR_USER}/{NC_COLLECTIVES_FOLDER}/"

# Destination folder on Nextcloud for the final ZIP

REMOTE_TARGET_FOLDER = f"/remote.php/dav/files/{ANCHOR_USER}/{NC_COLLECTIVES_BACKUP_FOLDER}/"

LOCAL_TEMP_DIR = "./temp_collectives"
TIMESTAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
ZIP_FILENAME = f"collectives_backup_{TIMESTAMP}.zip"

def download_recursive(remote_url, local_path):
    """
    Recursively downloads files and directories via WebDAV.
    """
    if not os.path.exists(local_path):
        os.makedirs(local_path)

    headers = {'Depth': '1'}
    response = requests.request('PROPFIND', remote_url, auth=(ANCHOR_USER, ANCHOR_APP_PW), headers=headers)

    #print(f"PROPFIND {remote_url} - Status: {response.status_code}")
    
    if response.status_code != 207:
        print(f"Error: Expected 207, got {response.status_code}")
        return

    # Extract items from XML response
    items = response.text.split('<d:response')[1:]
    #print(f"Found {len(items)} items")
    
    for item in items:
        href = item.split('<d:href>')[1].split('</d:href>')[0]
        decoded_href = unquote(href)
        full_remote_item_url = f"{NEXTCLOUD_URL}{href}"
        
        # Skip the current directory itself by comparing paths
        remote_path = remote_url.replace(NEXTCLOUD_URL, '')
        if decoded_href.rstrip('/') == unquote(remote_path).rstrip('/'):
            continue
            
        item_name = decoded_href.rstrip('/').split('/')[-1]
        local_item_path = os.path.join(local_path, item_name)

        if href.endswith('/'):
            download_recursive(full_remote_item_url, local_item_path)
        else:
            #print(f"Downloading: {item_name}")
            file_resp = requests.get(full_remote_item_url, auth=(ANCHOR_USER, ANCHOR_APP_PW))
            with open(local_item_path, 'wb') as f:
                f.write(file_resp.content)

def upload_zip(local_zip_path, remote_target_url):
    """
    Uploads the created ZIP file back to Nextcloud.
    """
    print(f"Uploading {local_zip_path} to {remote_target_url}...")
    with open(local_zip_path, 'rb') as f:
        response = requests.put(remote_target_url, data=f, auth=(ANCHOR_USER, ANCHOR_APP_PW))

    if response.status_code == 404:
        print(f"Error: Backup folder does not exist at {REMOTE_TARGET_FOLDER}")
        sys.exit(1)
    elif response.status_code in [201, 204]:
        print("Upload successful!")
    else:
        print(f"Upload failed with status: {response.status_code}")
        sys.exit(1)

def cleanup_old_backups():
    """
    Remove old backups if count exceeds NC_COLLECTIVES_BACKUP_COUNT.
    """
    if not NC_COLLECTIVES_BACKUP_COUNT:
        return
    
    max_backups = int(NC_COLLECTIVES_BACKUP_COUNT)
    backup_folder_url = f"{NEXTCLOUD_URL}{REMOTE_TARGET_FOLDER}"
    
    headers = {'Depth': '1'}
    response = requests.request('PROPFIND', backup_folder_url, auth=(ANCHOR_USER, ANCHOR_APP_PW), headers=headers)
    
    if response.status_code != 207:
        return
    
    # Extract backup files
    items = response.text.split('<d:response')[1:]
    backups = []
    
    for item in items:
        href = item.split('<d:href>')[1].split('</d:href>')[0]
        if 'collectives_backup_' in href and href.endswith('.zip'):
            # Extract last modified time
            if '<d:getlastmodified>' in item:
                modified = item.split('<d:getlastmodified>')[1].split('</d:getlastmodified>')[0]
                backups.append((href, modified))
    
    # Sort by date (oldest first)
    backups.sort(key=lambda x: x[1])
    
    # Delete oldest backups if exceeding limit
    if len(backups) > max_backups:
        to_delete = backups[:len(backups) - max_backups]
        for href, _ in to_delete:
            delete_url = f"{NEXTCLOUD_URL}{href}"
            print(f"Deleting old backup: {href}")
            requests.delete(delete_url, auth=(ANCHOR_USER, ANCHOR_APP_PW))

if __name__ == "__main__":
    # 1. Download all files
    print("Starting download...")
    download_recursive(f"{NEXTCLOUD_URL}{REMOTE_SOURCE_PATH}", LOCAL_TEMP_DIR)

    # 2. Create ZIP archive
    print("Creating ZIP archive...")
    shutil.make_archive(ZIP_FILENAME.replace('.zip', ''), 'zip', LOCAL_TEMP_DIR)

    # 3. Upload ZIP to Nextcloud
    target_url = f"{NEXTCLOUD_URL}{REMOTE_TARGET_FOLDER}{ZIP_FILENAME}"
    upload_zip(ZIP_FILENAME, target_url)

    # 4. Cleanup old backups
    cleanup_old_backups()

    # 5. Cleanup local files
    print("Cleaning up local temporary files...")
    shutil.rmtree(LOCAL_TEMP_DIR)
    os.remove(ZIP_FILENAME)

    print("Backup process finished.")
