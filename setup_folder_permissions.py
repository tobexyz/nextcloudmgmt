#!/usr/bin/env python3
"""Setup folder permissions based on YAML configuration."""

import requests
import yaml
import os
import sys
import time
import json
from requests.auth import HTTPBasicAuth
from webdav3.client import Client


NEXTCLOUD_URL = os.getenv("NC_URL")
ANCHOR_USER = os.getenv("NC_ANCHOR_USER")
ANCHOR_APP_PW = os.getenv("NC_ANCHOR_APP_PW")
ALL_MEMBERS_GROUP = os.getenv("NC_ALL_MEMBERS_GROUP")
ADMIN_GROUP = os.getenv("NC_ADMIN_GROUP")

SLEEP_TIME = 0.1

auth = HTTPBasicAuth(ANCHOR_USER, ANCHOR_APP_PW)
ocs_headers = {"OCS-APIRequest": "true", "Accept": "application/json"}


def sleep():
    time.sleep(SLEEP_TIME)


def ensure_group(group_name):
    """Create group if it doesn't exist."""
    resp = requests.get(
        f"{NEXTCLOUD_URL}/ocs/v1.php/cloud/groups",
        auth=auth, headers=ocs_headers
    )
    
    if resp.status_code != 200:
        print(f"❌ Failed to list groups: {resp.text}")
        return False
    
    groups = resp.json().get('ocs', {}).get('data', {}).get('groups', [])
    
    if group_name in groups:
        print(f"   ✅ Group '{group_name}' already exists")
        return True
    
    print(f"   Creating group '{group_name}'...")
    resp = requests.post(
        f"{NEXTCLOUD_URL}/ocs/v1.php/cloud/groups",
        auth=auth, headers=ocs_headers, data={"groupid": group_name}
    )
    
    if resp.status_code == 200:
        print(f"   ✅ Created group '{group_name}'")
        # Add anchor user to the group
        resp = requests.post(
            f"{NEXTCLOUD_URL}/ocs/v1.php/cloud/users/{ANCHOR_USER}/groups",
            auth=auth, headers=ocs_headers, data={"groupid": group_name}
        )
        if resp.status_code == 200:
            print(f"   ✅ Added anchor_user to '{group_name}'")
        return True
    else:
        print(f"   ❌ Failed to create group '{group_name}': {resp.text}")
        return False


def load_yaml_config(config_path):
    """Load and validate YAML configuration."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    if 'global_groups' not in config:
        config['global_groups'] = []
    if 'groupfolders' not in config:
        config['groupfolders'] = []
    
    return config


def folder_exists(client, folder_path):
    """Check if a folder exists."""
    return client.check(folder_path)


def create_folder(client, folder_path):
    """Create a folder path if it doesn't exist."""
    parts = folder_path.split('/')
    current_path = ""
    for part in parts:
        if not part:
            continue
        current_path += "/" + part
        if not client.check(current_path):
            client.mkdir(current_path)
            print(f"   Created: {current_path}")


def apply_acl_permissions(folder_path, groups, mask, permissions):
    """Apply ACL permissions to a folder."""
    headers = {'Content-Type': 'application/xml'} | ocs_headers
    xml_body = f"""<?xml version="1.0"?>
        <d:propertyupdate  xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns" xmlns:ocs="http://open-collaboration-services.org/ns">
          <d:set>
           <d:prop>
              <nc:acl-list> 
              <nc:acl>
              <nc:acl-mapping-type>group</nc:acl-mapping-type>
              <nc:acl-mapping-id>{{group_name}}</nc:acl-mapping-id>
              <nc:acl-mask>{mask}</nc:acl-mask>
              <nc:acl-permissions>{permissions}</nc:acl-permissions></nc:acl></nc:acl-list>
              </d:prop>
          </d:set>
        </d:propertyupdate>"""
    
    for group in groups:
        xml = xml_body.replace('{group_name}', group)
        response = requests.request(
            "PROPPATCH",
            f"{NEXTCLOUD_URL}/remote.php/dav/files/{ANCHOR_USER}/{folder_path}",
            auth=auth,
            data=xml,
            headers=headers
        )
        
        if response.status_code in [200, 207]:
            print(f"   ✅ ACL set for {group}: mask={mask}, permissions={permissions}")
        else:
            print(f"   ❌ Failed to set ACL for {group}: {response.status_code} {response.text}")
            return False
    return True


def apply_permissions_to_folder(folder_config, groupfolder_name):
    """Apply permissions to a single folder."""
    folder_path = f"{groupfolder_name}/{folder_config['path']}"
    
    print(f"\nProcessing folder: {folder_path}")
    
    # Create folder if it doesn't exist
    webdav_options = {
        'webdav_hostname': f"{NEXTCLOUD_URL}/remote.php/dav/groupfolders/{ANCHOR_USER}/{groupfolder_name}",
        'webdav_login': ANCHOR_USER,
        'webdav_password': ANCHOR_APP_PW,
    }
    client = Client(webdav_options)
    
    if not folder_exists(client, folder_path):
        create_folder(client, folder_path)
    else:
        print(f"   Folder already exists")
    
    sleep()
    
    # Apply block permissions (mask 0, permissions 0)
    if 'block' in folder_config and folder_config['block']:
        if not apply_acl_permissions(folder_path, folder_config['block'], 0, 0):
            return False
        sleep()
    
    # Apply read permissions (mask 1, permissions 1)
    if 'read' in folder_config and folder_config['read']:
        if not apply_acl_permissions(folder_path, folder_config['read'], 1, 1):
            return False
        sleep()
    
    # Apply write permissions (mask 31, permissions 31)
    if 'write' in folder_config and folder_config['write']:
        if not apply_acl_permissions(folder_path, folder_config['write'], 31, 31):
            return False
        sleep()
    
    return True


def process_config(config):
    """Process all folders from YAML config."""
    print("Starting folder permission setup...")
    
    # Collect all groups from config
    all_groups = set()
    all_groups.update(config.get('global_groups', []))
    
    for groupfolder in config.get('groupfolders', []):
        for folder_config in groupfolder.get('folders', []):
            if 'block' in folder_config:
                all_groups.update(folder_config['block'])
            if 'read' in folder_config:
                all_groups.update(folder_config['read'])
            if 'write' in folder_config:
                all_groups.update(folder_config['write'])
    
    # Ensure all groups exist
    print("\nEnsuring all groups exist...")
    for group in sorted(all_groups):
        if not ensure_group(group):
            return False
    
    # Process groupfolders
    for groupfolder in config.get('groupfolders', []):
        groupfolder_name = groupfolder['name']
        print(f"\n{'='*60}")
        print(f"Processing groupfolder: {groupfolder_name}")
        print(f"{'='*60}")
        
        for folder_config in groupfolder.get('folders', []):
            if not apply_permissions_to_folder(folder_config, groupfolder_name):
                print(f"❌ Failed to process folder: {folder_config['path']}")
                return False
    
    print("\n✅ All folders processed successfully!")
    return True


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Setup folder permissions from YAML config')
    parser.add_argument('config', help='Path to YAML configuration file')
    args = parser.parse_args()
    
    if not os.path.exists(args.config):
        print(f"❌ Config file not found: {args.config}")
        sys.exit(1)
    
    config = load_yaml_config(args.config)
    
    if not process_config(config):
        sys.exit(1)


if __name__ == "__main__":
    main()
