import requests
import caldav
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
ADMIN_GROUP= os.getenv("NC_ADMIN_GROUP")
QUOTA_GB_STR =  os.getenv("NC_QUOTA_GB")
QUOTA_GB = int(QUOTA_GB_STR)
PUBLIC_SUBFOLDER = os.getenv("NC_PUBLIC_SUBFOLDER")
RAW_SUBFOLDERS = os.getenv("NC_SUBFOLDERS")
SUBFOLDERS = [s.strip() for s in RAW_SUBFOLDERS.split(',')]
PUB_FOLDER_PREFIX = os.getenv("NC_PUB_FOLDER_PREFIX")
PRIV_FOLDER_PREFIX = os.getenv("NC_PRIV_FOLDER_PREFIX")

SLEEP_TIME = 0.1
# ---------------------


# Using the Anchor User for all API calls
auth = HTTPBasicAuth(ANCHOR_USER, ANCHOR_APP_PW)
ocs_headers = {"OCS-APIRequest": "true",               
               "Accept": "application/json"}




def sleep():
    print(f"💤Sleeping a bit...")
    time.sleep(SLEEP_TIME)


def grant_read_access(group_folder, subfolder):
    resp = requests.post(f"{NEXTCLOUD_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares",auth=auth, headers=ocs_headers, 
                     data={"path": f"{group_folder}/{subfolder}",
                         "shareType": 1,
                         "shareWith": f"{ALL_MEMBERS_GROUP}",
                         "permissions":17}) # 1 allow read + 16 allow share
    if resp.status_code != 200:
        print(f"❌ Failed to grant read access: {resp.text}")
        return False

    
    return True

def grant_write_access(group_name, group_folder, subfolder):   
    resp = requests.post(f"{NEXTCLOUD_URL}/ocs/v2.php/apps/files_sharing/api/v1/shares",auth=auth, headers=ocs_headers, 
                     data={"path": f"{group_folder}/{subfolder}",
                        "shareType": 1,
                         "shareWith": f"{group_name}",
                         "permissions":31}) # 31 full access
    if resp.status_code != 200:
        print(f"❌ Failed to grant write access: {resp.text}")
        return False
    return True

def grant_acl_access(group_name, group_folder, subfolder, mask, permisisons):
    headers = {'Content-Type': 'application/xml'} | ocs_headers
    xml_body=f"""<?xml version="1.0"?>
        <d:propertyupdate  xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns" xmlns:ocs="http://open-collaboration-services.org/ns">
          <d:set>
           <d:prop>
              <nc:acl-list> 
              <nc:acl>
              <nc:acl-mapping-type>group</nc:acl-mapping-type>
              <nc:acl-mapping-id>{group_name}</nc:acl-mapping-id>
              <nc:acl-mask>{mask}</nc:acl-mask>
              <nc:acl-permissions>{permisisons}</nc:acl-permissions></nc:acl></nc:acl-list>
              </d:prop>
          </d:set>
        </d:propertyupdate>"""
    response = requests.request(
        "PROPPATCH",
        f"{NEXTCLOUD_URL}/remote.php/dav/files/anchor_user/{group_folder}/{subfolder}",
        auth=auth,
        data=xml_body,
        headers=headers
    )
    
    if response.status_code in [200, 207]:
        print("✅ACL erfolgreich gesetzt!")
    else:
        print(f"❌ Fehler: {response.status_code} {response.text}")        
        return False
    return True

def create_group_folder(group_name):
    """
    Configures advanced permissions:
    - Main folder: Readable by everyone, writable by AK group.
    - Public subfolder: Inherits read access for everyone.
    - Internal subfolder: Hidden from everyone except the AK group.
    """
    print(f"Creating group folder...")
    folder_name = f"{group_name}"
    folder_req = requests.post(
        f"{NEXTCLOUD_URL}/apps/groupfolders/folders",
        auth=auth, headers=ocs_headers, data={"mountpoint": folder_name}
    )

    if folder_req.status_code != 200:
        print("❌ Error: Could not create group folder.")
        print(f"❌ API Error: Status {folder_req.status_code}")
        print(f"❌ Response: {folder_req.text}") 
        return False

    folder_id = folder_req.json()['ocs']['data']['id']
    print(f"   -> Folder ID: {folder_id}")
    sleep()
    # Set Quota
    quota_bytes = QUOTA_GB * 1024 * 1024 * 1024
    print(f"   -> Setting quota to {QUOTA_GB} GB in bytes {quota_bytes}...")
    resp = requests.post(
        f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/quota",
        auth=auth,
        headers=ocs_headers,
        data={"quota": quota_bytes},
        timeout=10
    )
    if resp.status_code != 200:
        print(f"❌ Failed to create quota: {resp.text}")
        return False
    sleep()
    # 1. Enable ACL support
    resp = requests.post(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/acl",
              auth=auth, headers=ocs_headers, data={"acl": "1"})
    if resp.status_code != 200:
        print(f"❌ Failed to enable acl: {resp.text}")
        return False
    # Set Permissions (31 = All permissions, 1 = Read only)
    # give anchor_user permission to the groupfolder!!
    #   
    print("Add Admin group to groupfolder")   
    resp = requests.post(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/groups",
                  auth=auth, headers=ocs_headers, data={"group": ADMIN_GROUP})
    if resp.status_code != 200:
        print(f"❌ Failed to create premisson to group admin on groupfolder: {resp.text} code: {resp.status_code}")
        return False
    resp = requests.post(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/groups/{ADMIN_GROUP}",
                  auth=auth, headers=ocs_headers, data={"permissions": 31})
    if resp.status_code != 200:
        print(f"❌ Failed to set premisson on groupfolder: {resp.text} code: {resp.status_code}")
        return False
    print("✅ Successfully added Admin group to groupfolder")   
    # Set Permissions (31 = All permissions, 1 = Read only)
    # give group permission to the groupfolder!!
    #      
    print("Add group {group_name} to groupfolder")   
    resp = requests.post(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/groups",
                  auth=auth, headers=ocs_headers, data={"group": group_name})
    if resp.status_code != 200:
        print(f"❌ Failed to create premisson to group admin on groupfolder: {resp.text} code: {resp.status_code}")
        return False
    
    resp = requests.post(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/groups/{group_name}",
                  auth=auth, headers=ocs_headers, data={"permissions": 31}) 
    if resp.status_code != 200:
        print(f"❌ Failed to set premisson on groupfolder: {resp.text} code: {resp.status_code}")
        return False
    print(f"✅ Successfully added group {group_name} to groupfolder")   

    if not grant_acl_access(group_name, group_name, "", "30" ,"31"): #write on root for group       
        print(f"❌ Failed to grant write for group on root")
        return False
    else:
        print(f"✅ Grant write for group on root")

    
    # Create subfolder structure via WebDAV

    options = {
        'webdav_hostname': f"{NEXTCLOUD_URL}/remote.php/dav/groupfolders/{ANCHOR_USER}/{folder_name}",
        'webdav_login': ANCHOR_USER,
        'webdav_password': ANCHOR_APP_PW,
    }

    client = Client(options)
    for sub in SUBFOLDERS:
        if sub != PUBLIC_SUBFOLDER:            
            subfolder_name = f"{PRIV_FOLDER_PREFIX}_{group_name}_{sub}"
        else:
            subfolder_name = f"{PUB_FOLDER_PREFIX}_{group_name}_{sub}"
        print(f"   -> Creating subfolder: {subfolder_name}")
        if client.check(subfolder_name):
            print(f"Folder '{subfolder_name}' already exists.")
        else:
            client.mkdir(subfolder_name)
            print(f"Successfully created folder: '{subfolder_name}'")
        sleep()
        
        if not grant_acl_access(group_name, group_name, subfolder_name, "30", "31"): #write on folder for group
            print(f"❌ Failed to grant write for group to subfolder {subfolder_name}")
            return False
        else:
            print(f"✅ Grant write access for group to subfolder {subfolder_name}")
        
        if not grant_write_access(group_name, folder_name,subfolder_name):
            print(f"❌ Failed to grant write for group to subfolder {subfolder_name}")
            return False
        else:
            print(f"✅ Grant write access for group to subfolder {subfolder_name}")
        if sub == PUBLIC_SUBFOLDER:            
            if not grant_read_access(folder_name,subfolder_name):
                print(f"❌ Failed to grant read access all to sub_folder {subfolder_name}")
                return False
            else:
                print(f"✅ Grant read access for all to subfolder {subfolder_name}")
        sleep()

    if not grant_acl_access(group_name, group_name, "", "30" ,"0"): #deny on root for group       
        print(f"❌ Failed to grant read for group on root")
        return False
    else:
        print(f"✅ Grant read for group on root")
	
    print("Remove Admin group from groupfolder")   
    resp = requests.delete(f"{NEXTCLOUD_URL}/apps/groupfolders/folders/{folder_id}/groups/{ADMIN_GROUP}",
                  auth=auth, headers=ocs_headers)
    if resp.status_code != 200:
        print(f"❌ Failed to remove admin group from groupfolder: {resp.text} code: {resp.status_code}")
        return False
    print(f"✅ Successfully removed admin group from groupfolder")   



    return True
        



def create_and_share_calendar(calendar_name, share_with_group):
    """
    Creates a new calendar via WebDAV and shares it with a group.
    """
    #Create Calendar via CalDAV
    print(f"Creating calendar in anchor account...")
    try:
        client = caldav.DAVClient(f"{NEXTCLOUD_URL}/remote.php/dav", username=ANCHOR_USER, password=ANCHOR_APP_PW)
        principal = client.principal()
        cal_id=f"cal_{calendar_name.lower()}"
        new_cal = principal.make_calendar(name=f"{calendar_name}", cal_id=cal_id)
        print(f"✅ Calendar created: {new_cal.url}")
    except Exception as e:
        print(f"⚠️ Calendar info: {e} (It might already exist)")
        return False
    
    if not share_calendar_with_group(cal_id, group_name, True):
        return False
    if not share_calendar_with_group(cal_id, ALL_MEMBERS_GROUP, False):
        return False
    return True



def share_calendar_with_group(calendar_id, group_name, write_access):
    cal_url = f"{NEXTCLOUD_URL}/remote.php/dav/calendars/{ANCHOR_USER}/{calendar_id}/"

    # 2. Share it using the DAV share XML (Standard Webdav XML format)
    # Share 1: Full Access for the Group
    share_xml = f"""<?xml version="1.0" encoding="utf-8" ?>
    <O:share xmlns:D="DAV:" xmlns:O="http://owncloud.org/ns">
      <O:set>
        <D:href>principal:principals/groups/{group_name}</D:href>
        <O:share-type>1</O:share-type>        
      </O:set>
    </O:share>"""
   
    resp = requests.post(
        cal_url, auth=auth, data=share_xml,
        headers={'Content-Type': 'text/xml'}
    )

    if resp.status_code != 200:
        print(f"   ❌ Failed to share calendar with group: {resp.text}")
        return False
    else:
        print(f"✅ Successfully shared calendar with group {group_name}")

    if not write_access:
        return True
    
    #grant write 
    share_xml = f"""<?xml version="1.0" encoding="utf-8" ?>
    <x4:share xmlns:x4="http://owncloud.org/ns">
      <x4:set>
        <x0:href xmlns:x0="DAV:">principal:principals/groups/{group_name}</x0:href>
        <x4:read-write/>
      </x4:set>
    </x4:share>"""
   
    resp = requests.post(
        cal_url, auth=auth, data=share_xml,
        headers={'Content-Type': 'text/xml'}
    )

    if resp.status_code != 200:
        print(f"   ❌ Failed to grant calendar write to group: {resp.text}")
        return False
    else:
        print(f"✅ Successfully  granted calendar write to group {group_name}")
    
    return True


def create_circle_and_collective(group_name):
    # Note: Circles are the permission layer for Collectives
    # circles are automatically created by collective 
    #print(f"Creating Circle for {group_name}...")
    #circle_name=f"Circle_{group_name}"
    #circle_resp = requests.post(
    #    f"{NEXTCLOUD_URL}/ocs/v2.php/apps/circles/circles",
    #    auth=auth,
    #    headers=ocs_headers,
    #    data={
    #        "name": circle_name,
    #        "config": 1,  # 1 = Secret (Mitglieder werden direkt eingebunden)
    #        "description": f"Circle for {group_name} Collective",
    #        "source": 1   # Signalisiert eine manuelle/App-Erstellung            
    #    }
    #)
    #
    #"personal": 0            

    #if circle_resp.status_code != 200 and circle_resp.status_code != 201:        
    #    print(f"❌ Failed to create Circle: {circle_resp.text}")
    #    return False
    #else:
    #    print(f"✅ Successfully created circle for group: {circle_name}")


    #circle_id = circle_resp.json()['ocs']['data']['id']
    #print(f"✅ Circle created: {circle_id}")

    #sleep()
    # Create the Collective
    print(f"Creating Collective '{group_name}'...")
    resp = requests.post(
        f"{NEXTCLOUD_URL}/ocs/v2.php/apps/collectives/api/v1.0/collectives",
        auth=auth,
        headers=ocs_headers,
        data={
            "name": group_name,            
            "emoji":"👥"            
        }
    )

    if resp.status_code == 200:
        print(f"✅ Collective created successfully!")
    else:
        print(f"❌ Failed to create Collective: {resp.text}")
        return False
    
    collective_id = resp.json()['ocs']['data']['collective']['id']
    circle_id = resp.json()['ocs']['data']['collective']['circleId']
    print(f"✅ Collective created: {collective_id}")
    print(f"✅ Circle created: {circle_id}")
    
    sleep()
    print(f"set collective to allow only moderators and administrators to edit")
    resp = requests.put(
        f"{NEXTCLOUD_URL}/ocs/v2.php/apps/collectives/api/v1.0/collectives/{collective_id}/editLevel",
        auth=auth,
        headers=ocs_headers,
        data={"level": 4})
    

    if resp.status_code == 200:
        print(f"✅ Collective edit right to moderators and administrators successfully set!")
    else:
        print(f"❌ Failed set edit right to moderators and administrators on collective: {resp.text}")
        return False

    sleep()
    # contributor right to group
    if not add_group_to_circle(circle_id,group_name, 4):
        return False
    # Member right to all
    if not add_group_to_circle(circle_id,ALL_MEMBERS_GROUP, 1):
        return False


    return True


def add_group_to_circle(circle_id, group_name, level):
     # Add the Group to the Circle
    print(f"   -> Adding group '{group_name}' to Circle...")
    payload = json.dumps({"userId": group_name,"type":2})
    headers = ocs_headers | {"content-type":"application/json"}    
    resp = requests.post(
        f"{NEXTCLOUD_URL}/ocs/v2.php/apps/circles/circles/{circle_id}/members",
        auth=auth,
        headers=headers,
        data=payload
    )
    if resp.status_code == 200:
        print(f"✅ Group added to Circle")
    else:
        print(f"❌ Failed to add group to Cricle: {resp.text}")
        return False
    sleep()
    member_id= resp.json()['ocs']['data']['id']
    print(f"member_id: {member_id}")
    
    
    
    if level == 1:
        print(f"✅ new groups already on level 1 no need to change the level for the group")
        return True
    set_grant_level_of_member(circle_id, member_id, level)
    
    return True

def set_grant_level_of_member(circle_id, member_id, level):
    # Grant level to member
    print(f"Granting level {level} to member {member_id} of circle {circle_id}")
    headers = ocs_headers | {"content-type":"application/json"} 
    resp = requests.put(
        f"{NEXTCLOUD_URL}/ocs/v2.php/apps/circles/circles/{circle_id}/members/{member_id}/level",
        auth=auth,
        headers=headers,
        data=json.dumps({"level": level})
    )
    if resp.status_code == 200:
        print(f"✅ Member {member_id} granted as {level} to Circle")
    else:
        print(f"❌ Failed to grant {level} right to of member {member_id} to Cricle: {resp.text}")
        return False
    return True

def create_group(group_name):
    # 1. Create the User Group
    print(f"Creating group '{group_name}'...")
    response = requests.post(
        f"{NEXTCLOUD_URL}/ocs/v1.php/cloud/groups",
        auth=auth, headers=ocs_headers, data={"groupid": group_name}
    )

    if response.status_code == 200:
        print(f"✅ Group created: {group_name}")
    else:
        print("❌ Error: Could not create group folder.")
        print(f"❌ API Error: Status {response.status_code}")
        return False
    sleep()
    # 2. Add Anchor User to the new group (Safety net)
    print(f"Adding anchor user to '{group_name}'...")
    response = requests.post(
        f"{NEXTCLOUD_URL}/ocs/v1.php/cloud/users/{ANCHOR_USER}/groups",
        auth=auth, headers=ocs_headers, data={"groupid": group_name}
    )
    if response.status_code == 200:
        print(f"✅ Added anchor_user to the group")
    else:
        print("❌ Error: Could not add anchor user to the group.")
        print(f"❌ API Error: Status {response.status_code}")
        return False
    return True


def create_talk_room(group_name):
    print(f"Creating Talk room for group '{group_name}'...")
    headers = ocs_headers | {"content-type":"application/json"}    
    resp = requests.post(f"{NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v4/room",auth=auth, headers=headers, 
                     data=json.dumps({"roomType": 2, 
                                      "roomName": group_name,
                                      "listable":1,
                                      "participants":{"groups":[group_name]}}
                                      ))
    if resp.status_code == 200 or resp.status_code == 201:
        print(f"✅ Talk room for group {group_name} created.")
    else:
        print(f"❌ Failed to create Talk room for group: {resp.text}")
        return False
    return True    





def run_group_setup(group_name):
    """
    Automates the creation of a Nextcloud group, group folder with subfolders,
    and a shared calendar owned by the anchor user.
    """

    print(f"🚀 Starting automation for: {group_name}")
    result = create_group(group_name)    
    
    if not result:
        print("❌ Stopping setup due to group creation failure.")
        return False

    result = create_group_folder(group_name)

    if not result:
        print("❌ Stopping setup due to folder creation failure.")
        return False

    result = create_circle_and_collective(group_name)

    if not result:
        print("❌ Stopping setup due to collective creation failure.")
        return False

    result = create_and_share_calendar(group_name,group_name)

    if not result:
        print("❌ Stopping setup due to calendar creation failure.")
        return False
    
    result = create_talk_room(group_name)
    if not result:
        print("❌ Stopping setup due to talk romm creation failure.")
        return False
    return True


if __name__ == "__main__":
    group_name = input("Enter name for new group (e.g., AK_Strategy): ").strip()
    if group_name:
        if run_group_setup(group_name):
            print(f"\n✨ Success! Group '{group_name}' is technically ready.")
        else:
            print(f"\n💥 Setup failed.")

    else:
        print("Abort: No group name provided.")