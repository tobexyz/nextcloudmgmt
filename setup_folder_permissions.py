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
ANCHOR_GROUP = os.getenv("NC_ANCHOR_GROUP", "Anchor_Group")

SLEEP_TIME = 0.1

auth = HTTPBasicAuth(ANCHOR_USER, ANCHOR_APP_PW)
ocs_headers = {"OCS-APIRequest": "true", "Accept": "application/json"}


def sleep():
    time.sleep(SLEEP_TIME)


def remove_share(group_folder, subfolder, share_with):
    """Remove a share from a folder."""
    # Get existing shares - use full path
    full_path = f"/{group_folder}/{subfolder}"
    resp = requests.get(
        f"{NEXTCLOUD_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares?path={full_path}&reshares=true",
        auth=auth, headers=ocs_headers
    )
    
    if resp.status_code != 200:
        print(f"   ❌ Failed to get shares: {resp.text}")
        return False
    
    shares = resp.json().get('ocs', {}).get('data', [])
    
    if not shares:
        print(f"   ℹ️  No shares found for {full_path}")
        return True
    
    for share in shares:
        share_with_user = share.get('share_with')
        share_type = share.get('share_type')
        
        # Check if this is the share we want to remove
        # share_type 1 = group share, share_type 0 = user share
        if share_with_user == share_with or (share_type == 1 and share_with == ALL_MEMBERS_GROUP):
            # Remove the share
            share_id = share.get('id')
            resp = requests.delete(
                f"{NEXTCLOUD_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}",
                auth=auth, headers=ocs_headers
            )
            if resp.status_code == 200:
                print(f"   ✅ Removed share {share_id} for {share_with_user} (type: {share_type})")
            else:
                print(f"   ❌ Failed to remove share {share_id}: {resp.text}")
                return False
    
    return True


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


def read_acl_permissions(group_folder, subfolder):
    """Read ACL permissions for a folder."""
    headers = {'Content-Type': 'application/xml'} | ocs_headers
    xml_body = """<?xml version="1.0"?>
        <d:propfind  xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns" xmlns:ocs="http://open-collaboration-services.org/ns">
          <d:prop>
             <nc:acl-list/> 
          </d:prop>
        </d:propfind>"""
    
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{ANCHOR_USER}/{group_folder}/{subfolder}"
    response = requests.request(
        "PROPFIND",
        url,
        auth=auth,
        data=xml_body,
        headers=headers
    )
    
    if response.status_code in [200, 207]:
        print(f"   📋 ACL for {group_folder}/{subfolder}:")
        #print(f"   {response.text}")
        return response.text
    else:
        print(f"   ❌ Failed to read ACL: {response.status_code} {response.text}")
        return None


def parse_existing_acls(acl_xml):
    """Parse existing ACL XML to extract current ACL entries."""
    import re
    acls = []
    if not acl_xml:
        return acls
    
    # Extract existing ACL entries
    acl_pattern = r'<nc:acl><nc:acl-mapping-type>([^<]+)</nc:acl-mapping-type><nc:acl-mapping-id>([^<]+)</nc:acl-mapping-id><nc:acl-mask>([^<]+)</nc:acl-mask><nc:acl-permissions>([^<]+)</nc:acl-permissions></nc:acl>'
    matches = re.findall(acl_pattern, acl_xml)
    
    for mapping_type, mapping_id, mask, permissions in matches:
        acls.append({
            'type': mapping_type,
            'id': mapping_id,
            'mask': mask,
            'permissions': permissions
        })
    
    return acls


def apply_acl_permissions(group_folder, subfolder, permissions_list):
    """Apply multiple ACL permissions to a folder by merging with existing ones."""
    # Read existing ACLs
    existing_xml = read_acl_permissions(group_folder, subfolder)
    existing_acls = parse_existing_acls(existing_xml)
    
    # Build merged ACL list
    merged_acls = existing_acls.copy()
    
    # Add/update permissions from the list
    for perm in permissions_list:
        group = perm['group']
        mask = perm['mask']
        permissions = perm['permissions']
        
        # Check if ACL for this group already exists
        found = False
        for acl in merged_acls:
            if acl['id'] == group:
                acl['mask'] = mask
                acl['permissions'] = permissions
                found = True
                break
        
        if not found:
            merged_acls.append({
                'type': 'group',
                'id': group,
                'mask': mask,
                'permissions': permissions
            })
    
    # Build XML with all ACLs
    headers = {'Content-Type': 'application/xml'} | ocs_headers
    acl_entries = ''.join([
        f'<nc:acl><nc:acl-mapping-type>{acl["type"]}</nc:acl-mapping-type><nc:acl-mapping-id>{acl["id"]}</nc:acl-mapping-id><nc:acl-mask>{acl["mask"]}</nc:acl-mask><nc:acl-permissions>{acl["permissions"]}</nc:acl-permissions></nc:acl>'
        for acl in merged_acls
    ])
    
    xml_body = f"""<?xml version="1.0"?>
<d:propertyupdate xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
 <d:set>
 <d:prop>
 <nc:acl-list>
 {acl_entries}
 </nc:acl-list>
 </d:prop>
 </d:set>
</d:propertyupdate>"""
    
    url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{ANCHOR_USER}/{group_folder}/{subfolder}"
    response = requests.request(
        "PROPPATCH",
        url,
        auth=auth,
        data=xml_body,
        headers=headers
    )
    
    if response.status_code in [200, 207]:
        print(f"   ✅ ACLs updated for {len(merged_acls)} groups")
        return True
    else:
        print(f"   ❌ Failed to update ACLs: {response.status_code} {response.text}")
        return False


def apply_permissions_to_folder(folder_config, groupfolder_name):
    """Apply permissions to a single folder."""
    folder_path = folder_config['path']
    
    print(f"\nProcessing folder: {groupfolder_name}/{folder_path}")
    
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
    
    # Remove share from parent folder to avoid override
    parent_path = '/'.join(folder_path.split('/')[:-1]) if '/' in folder_path else ''
    if parent_path:
        print(f"   Removing share from parent: {parent_path}")
        remove_share(groupfolder_name, parent_path, ALL_MEMBERS_GROUP)
    
    sleep()
    
    # Collect all permissions to apply
    permissions_list = []
    
    # Apply block permissions (mask 31, permissions 0 - deny read)
    # Exclude anchor_user from block permissions
    block_groups = [g for g in folder_config.get('block', []) if g != ANCHOR_GROUP]
    for group in block_groups:
        permissions_list.append({'group': group, 'mask': 31, 'permissions': 0})
    
    # Apply read permissions (mask 31, permissions 1)
    # Exclude anchor_group from read permissions (they have full access by default)
    read_groups = [g for g in folder_config.get('read', []) if g != ANCHOR_GROUP]
    for group in read_groups:
        permissions_list.append({'group': group, 'mask': 31, 'permissions': 1})
    
    # Apply write permissions (mask 31, permissions 31) - exclude anchor_group
    write_groups = [g for g in folder_config.get('write', []) if g != ANCHOR_GROUP]
    for group in write_groups:
        permissions_list.append({'group': group, 'mask': 31, 'permissions': 31})
    
    # Add Anchor_Group with full access (mask 31, permissions 31) for administration
    permissions_list.append({'group': ANCHOR_GROUP, 'mask': 31, 'permissions': 31})
    
    # Apply all permissions in one request
    if not apply_acl_permissions(groupfolder_name, folder_path, permissions_list):
        return False
    
    sleep()
    
    # Read back ACL for verification
    read_acl_permissions(groupfolder_name, folder_path)
    
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
    
    # Add anchor group
    all_groups.add(ANCHOR_GROUP)
    
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
    parser.add_argument('config', nargs='?', default='sample-permissions.yaml', help='Path to YAML configuration file')
    args = parser.parse_args()
    
    if not os.path.exists(args.config):
        print(f"❌ Config file not found: {args.config}")
        sys.exit(1)
    
    config = load_yaml_config(args.config)
    
    if not process_config(config):
        sys.exit(1)


if __name__ == "__main__":
    main()
