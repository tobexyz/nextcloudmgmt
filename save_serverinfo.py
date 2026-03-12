import requests
import datetime
import json
import os

NC_URL = os.getenv("NC_URL")
NC_USER = os.getenv("NC_ANCHOR_USER")
NC_APP_PW = os.getenv("NC_ANCHOR_APP_PW")

DEST_FOLDER = os.getenv("NC_STATS_DIR")

api_url = f"{NC_URL}/ocs/v2.php/apps/serverinfo/api/v1/info?format=json"
headers = {"OCS-APIRequest": "true"}

try:
    response = requests.get(api_url, auth=(NC_USER, NC_APP_PW), headers=headers)
    response.raise_for_status()
    raw_json = response.text  
except Exception as e:
    print(f"Fehler beim Abrufen der API: {e}")
    exit(1)

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
filename = f"stats_raw_{timestamp}.json"

webdav_url = f"{NC_URL}/remote.php/dav/files/{NC_USER}/{DEST_FOLDER}/{filename}"

try:
    upload_res = requests.put(webdav_url, data=raw_json, auth=(NC_USER, NC_APP_PW))
    upload_res.raise_for_status()
    print(f"Success! Upload finished: {filename}")
except Exception as e:
    print(f"Error during upload: {e}")

