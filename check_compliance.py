#!/usr/bin/env python3
"""
Nextcloud Rights Compliance Checker

Checks group folder, calendar, and collective configurations against expected state.
"""

import argparse
import datetime
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

import caldav
import requests
import yaml
from requests.auth import HTTPBasicAuth
from xml.etree import ElementTree as ET

# Environment variables
NC_URL = os.getenv("NC_URL")
NC_ANCHOR_USER = os.getenv("NC_ANCHOR_USER")
NC_ANCHOR_APP_PW = os.getenv("NC_ANCHOR_APP_PW")
NC_ALL_MEMBERS_GROUP = os.getenv("NC_ALL_MEMBERS_GROUP", "all_users")
NC_ADMIN_GROUP = os.getenv("NC_ADMIN_GROUP", "admin")
NC_ANCHOR_GROUP = os.getenv("NC_ANCHOR_GROUP", "Anchor_Group")
NC_PUB_FOLDER_PREFIX = os.getenv("NC_PUB_FOLDER_PREFIX", "01")
NC_PRIV_FOLDER_PREFIX = os.getenv("NC_PRIV_FOLDER_PREFIX", "02")
NC_STATS_DIR = os.getenv("NC_STATS_DIR")


def load_config_files(file_paths):
    """Load and merge multiple YAML config files."""
    merged = {"global_groups": [], "groupfolders": []}
    seen_folders = {}

    for path in file_paths:
        with open(path, 'r') as f:
            config = yaml.safe_load(f)

        if not config:
            continue

        # Merge global_groups
        if "global_groups" in config:
            merged["global_groups"].extend(config["global_groups"])

        # Merge groupfolders by name
        if "groupfolders" in config:
            for gf in config["groupfolders"]:
                name = gf["name"]
                if name not in seen_folders:
                    seen_folders[name] = {"name": name, "folders": []}
                # Build folder lookup for merging
                folder_map = {f["path"]: f for f in seen_folders[name]["folders"]}
                for folder in gf.get("folders", []):
                    folder_map[folder["path"]] = folder
                seen_folders[name]["folders"] = list(folder_map.values())

    merged["groupfolders"] = list(seen_folders.values())
    return merged


def glob_configs(dir_path):
    """Glob YAML files from directory."""
    patterns = ["*.yaml", "*.yml"]
    files = []
    for p in patterns:
        files.extend(glob.glob(os.path.join(dir_path, p)))
    return sorted(files)


def build_expected_state(config):
    """Build expected state from config using implicit WG model rules."""
    expected = {
        "group_folders": {},
        "calendars": {},
        "collectives": {}
    }

    for gf in config.get("groupfolders", []):
        group_name = gf["name"]
        wg_group = gf.get("group", group_name)  # override: actual group ID if different from folder name
        folders = gf.get("folders", [])

        # Group folder membership
        expected["group_folders"][group_name] = {
            "wg_group": wg_group,
            "membership": {wg_group: 31, NC_ANCHOR_GROUP: 31},
            "membership_absent": [NC_ADMIN_GROUP],
            "root_acl": [{"group": wg_group, "mask": 30, "permissions": 0}],
            "subfolders": {}
        }

        # Calendar shares
        expected["calendars"][group_name] = {
            "shares": [
                {"group": wg_group, "writable": True},
                {"group": NC_ALL_MEMBERS_GROUP, "writable": False}
            ]
        }

        # Collective members
        expected["collectives"][group_name] = {
            "members": [
                {"group": wg_group, "level": 4},
                {"group": NC_ALL_MEMBERS_GROUP, "level": 1}
            ]
        }
        expected["collectives"][group_name] = {
            "members": [
                {"group": group_name, "level": 4},
                {"group": NC_ALL_MEMBERS_GROUP, "level": 1}
            ]
        }

        # Process each subfolder
        for folder in folders:
            path = folder["path"]
            shares = []

            # Build ACL as dict (group -> {mask, permissions}), YAML overrides win
            acl_map = {}

            # Implicit: WG group gets write on all subfolders
            acl_map[wg_group] = {"mask": 30, "permissions": 31}

            # YAML overrides (applied after implicit, so they override)
            for group in folder.get("block", []):
                acl_map[group] = {"mask": 31, "permissions": 0}
            for group in folder.get("read", []):
                acl_map[group] = {"mask": 31, "permissions": 1}
            for group in folder.get("write", []):
                acl_map[group] = {"mask": 31, "permissions": 31}

            # Anchor_Group always full access (overrides any YAML setting)
            acl_map[NC_ANCHOR_GROUP] = {"mask": 31, "permissions": 31}

            acl = [{"group": g, **v} for g, v in acl_map.items()]

            # Public folder shares (starts with 01_) — only public folders get shares
            if path.startswith(f"{NC_PUB_FOLDER_PREFIX}_"):
                shares.append({
                    "share_with": NC_ALL_MEMBERS_GROUP,
                    "share_type": 1,
                    "permissions": 17
                })
                shares.append({
                    "share_with": wg_group,
                    "share_type": 1,
                    "permissions": 31
                })

            # ACL-managed folders should NOT have WG shares (shares override ACLs)

            expected["group_folders"][group_name]["subfolders"][path] = {
                "acl": acl,
                "shares": shares
            }

    return expected


def ocs_get(path, params=None):
    """Make OCS API GET request."""
    url = f"{NC_URL}/ocs/v2.php/{path.lstrip('/')}"
    try:
        resp = requests.get(url, auth=HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW),
                           headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                           params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  Warning: OCS request failed for {path}: {e}")
        return None


def propfind_acl(webdav_path):
    """Make PROPFIND request to fetch ACLs, return parsed ACL list."""
    url = f"{NC_URL}/remote.php/dav/files/{NC_ANCHOR_USER}/{webdav_path.lstrip('/')}"
    headers = {"Depth": "0", "Content-Type": "application/xml"}
    body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <nc:acl-list/>
  </d:prop>
</d:propfind>"""
    try:
        resp = requests.request("PROPFIND", url,
                               auth=HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW),
                               headers=headers, data=body, timeout=30)
        resp.raise_for_status()
        xml = resp.text
        acls = []
        pattern = re.compile(
            r'<nc:acl-mapping-type>([^<]+)</nc:acl-mapping-type>'
            r'<nc:acl-mapping-id>([^<]+)</nc:acl-mapping-id>'
            r'(?:<nc:acl-mapping-display-name>[^<]*</nc:acl-mapping-display-name>)?'
            r'<nc:acl-mask>([^<]+)</nc:acl-mask>'
            r'<nc:acl-permissions>([^<]+)</nc:acl-permissions>'
        )
        for match in pattern.finditer(xml):
            acl_type, acl_id, mask, perms = match.groups()
            if acl_type == "group":
                acls.append({"type": "group", "group": acl_id, "mask": int(mask), "permissions": int(perms)})
            elif acl_type == "user":
                acls.append({"type": "user", "group": acl_id, "mask": int(mask), "permissions": int(perms)})
        return acls
    except Exception as e:
        print(f"  Warning: PROPFIND failed for {webdav_path}: {e}")
        return []


def discover_subfolders(folder_name):
    """Discover all subfolders of a group folder via PROPFIND Depth:infinity."""
    url = f"{NC_URL}/remote.php/dav/files/{NC_ANCHOR_USER}/{folder_name}"
    headers = {"Depth": "infinity", "Content-Type": "application/xml"}
    body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:resourcetype/></d:prop>
</d:propfind>"""
    try:
        resp = requests.request("PROPFIND", url,
                               auth=HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW),
                               headers=headers, data=body, timeout=60)
        resp.raise_for_status()
        ns = {"d": "DAV:"}
        root = ET.fromstring(resp.text)
        subfolders = set()
        base_path = f"/remote.php/dav/files/{NC_ANCHOR_USER}/{folder_name}"
        for response in root.findall(".//d:response", ns):
            href_el = response.find("d:href", ns)
            # Only include directories (collections)
            restype = response.find(".//d:resourcetype/d:collection", ns)
            if href_el is None or restype is None:
                continue
            href = href_el.text.rstrip("/")
            if href == base_path or href == base_path.rstrip("/"):
                continue
            # Extract relative path
            rel = href[len(base_path):].strip("/")
            if rel:
                subfolders.add(rel)
        return subfolders
    except Exception as e:
        print(f"    Warning: Could not discover subfolders for {folder_name}: {e}")
        return set()


def enrich_expected_from_server(expected):
    """Fetch all group folders from server and add implicit expected state for any not in YAML."""
    auth = HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW)
    try:
        resp = requests.get(
            f"{NC_URL}/apps/groupfolders/folders",
            auth=auth, headers={"OCS-APIRequest": "true", "Accept": "application/json"}, timeout=30
        )
        resp.raise_for_status()
        folders_data = resp.json().get("ocs", {}).get("data", {})
        if isinstance(folders_data, dict):
            folders_data = list(folders_data.values())
    except Exception as e:
        print(f"  Warning: Could not enrich from server: {e}")
        return expected

    for f in folders_data:
        name = f.get("mount_point", "")
        if not name or name in expected["group_folders"]:
            continue
        # Add implicit model for this group folder
        expected["group_folders"][name] = {
            "wg_group": name,
            "membership": {name: 31, NC_ANCHOR_GROUP: 31},
            "membership_absent": [NC_ADMIN_GROUP],
            "root_acl": [{"group": name, "mask": 30, "permissions": 0}],
            "subfolders": {}
        }
        expected["calendars"][name] = {
            "shares": [
                {"group": name, "writable": True},
                {"group": NC_ALL_MEMBERS_GROUP, "writable": False}
            ]
        }
        expected["collectives"][name] = {
            "members": [
                {"group": name, "level": 4},
                {"group": NC_ALL_MEMBERS_GROUP, "level": 1}
            ]
        }

    return expected


def fetch_actual_state(expected):
    """Fetch actual state from Nextcloud server."""
    actual = {
        "group_folders": {},
        "calendars": {},
        "collectives": {}
    }

    auth = HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW)
    ocs_headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

    # 1. Fetch group folders
    print("Fetching group folders...")
    try:
        resp = requests.get(
            f"{NC_URL}/apps/groupfolders/folders",
            auth=auth, headers={"OCS-APIRequest": "true", "Accept": "application/json"}, timeout=30
        )
        resp.raise_for_status()
        folders_data = resp.json().get("ocs", {}).get("data", {})
    except Exception as e:
        print(f"  Warning: Failed to fetch group folders: {e}")
        folders_data = {}

    # Normalize to list (API returns dict keyed by folder ID)
    if isinstance(folders_data, dict):
        folders_data = list(folders_data.values())

    # Build name -> id map
    name_to_id = {f["mount_point"]: f["id"] for f in folders_data}

    for name, expected_gf in expected.get("group_folders", {}).items():
        print(f"  Fetching group folder: {name}...")
        time.sleep(0.1)

        # Find matching folder by mount_point
        folder_id = name_to_id.get(name)
        if folder_id is None:
            print(f"  Warning: Group folder '{name}' not found on server")
            continue

        # Get groups/permissions for this folder
        membership = {}
        for f in folders_data:
            if f["id"] == folder_id:
                groups = f.get("groups", {})
                for group_name, perm in groups.items():
                    membership[group_name] = int(perm)
                break

        # Fetch root ACL
        root_acl = propfind_acl(name)

        # Discover all subfolders via PROPFIND Depth:1 recursively
        known_subfolders = set(expected_gf.get("subfolders", {}).keys())
        discovered_subfolders = discover_subfolders(name)
        all_subfolders = known_subfolders | discovered_subfolders

        subfolders = {}
        for subfolder_name in sorted(all_subfolders):
            print(f"    Fetching subfolder: {subfolder_name}...")
            time.sleep(0.1)

            # Fetch ACL for subfolder
            subfolder_acl = propfind_acl(f"{name}/{subfolder_name}")

            # Fetch shares for subfolder
            shares = []
            try:
                share_url = f"/ocs/v2.php/apps/files_sharing/api/v1/shares?path=/{name}/{subfolder_name}&reshares=true"
                share_resp = requests.get(
                    f"{NC_URL}{share_url}",
                    auth=auth, headers=ocs_headers, timeout=30
                )
                share_resp.raise_for_status()
                share_data = share_resp.json().get("ocs", {}).get("data", [])
                for s in share_data:
                    share_entry = {
                        "share_with": s.get("share_with"),
                        "share_type": s.get("share_type"),
                        "permissions": s.get("permissions"),
                    }
                    # share_type 3 = public link
                    if s.get("share_type") == 3:
                        share_entry["has_password"] = bool(s.get("password"))
                        share_entry["expiration"] = s.get("expiration")
                        share_entry["label"] = s.get("label", s.get("name", ""))
                    shares.append(share_entry)
            except Exception as e:
                print(f"    Warning: Failed to fetch shares for {subfolder_name}: {e}")

            subfolders[subfolder_name] = {
                "acl": subfolder_acl,
                "shares": shares
            }

        actual["group_folders"][name] = {
            "membership": membership,
            "root_acl": root_acl,
            "subfolders": subfolders
        }

    # 2. Fetch calendar shares
    print("Fetching calendars...")
    try:
        client = caldav.DAVClient(url=f"{NC_URL}/remote.php/dav", username=NC_ANCHOR_USER, password=NC_ANCHOR_APP_PW)
        principal = client.principal()
        calendars = principal.calendars()
    except Exception as e:
        print(f"  Warning: Failed to fetch calendars: {e}")
        calendars = []

    for cal in calendars:
        cal_name = cal.get_display_name()
        # Check if calendar matches any expected group folder name
        matched_group = None
        for expected_name in expected.get("calendars", {}):
            if expected_name in cal_name:
                matched_group = expected_name
                break
        if not matched_group:
            continue

        print(f"  Fetching calendar shares: {cal_name}...")
        time.sleep(0.1)

        shares = []
        try:
            # Fetch shares via PROPFIND with o:invite
            propfind_xml = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:o="http://owncloud.org/ns">
  <d:prop><o:invite/></d:prop>
</d:propfind>"""
            resp = requests.request(
                "PROPFIND", str(cal.url), 
                auth=HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW),
                headers={"Content-Type": "application/xml", "Depth": "0"},
                data=propfind_xml, timeout=30
            )
            ns = {"d": "DAV:", "o": "http://owncloud.org/ns", "cs": "http://calendarserver.org/ns/"}
            root = ET.fromstring(resp.text)
            for user in root.findall(".//o:invite/o:user", ns):
                href_el = user.find("d:href", ns)
                access_el = user.find("o:access", ns)
                if href_el is None:
                    continue
                href = href_el.text or ""
                group_name = href.split("/")[-1]
                writable = access_el.find("o:read-write", ns) is not None if access_el is not None else False
                shares.append({"group": group_name, "writable": writable})
        except Exception as e:
            print(f"    Warning: Failed to fetch invites for {cal_name}: {e}")

        actual["calendars"][matched_group] = {"shares": shares}

    # 3. Fetch collective/circle membership
    print("Fetching collectives...")
    collectives = ocs_get("/apps/collectives/api/v1.0/collectives")
    if collectives and "ocs" in collectives and "data" in collectives["ocs"]:
        circles_data = collectives["ocs"]["data"]
    else:
        circles_data = []

    for coll in circles_data:
        coll_name = coll.get("name") or coll.get("title", "")
        matched_group = None
        for expected_name in expected.get("collectives", {}):
            if expected_name in coll_name:
                matched_group = expected_name
                break
        if not matched_group:
            continue

        print(f"  Fetching collective members: {coll_name}...")
        time.sleep(0.1)

        # Get circle ID
        circle_id = coll.get("id") or coll.get("circle_id")
        if not circle_id:
            continue

        members = []
        members_data = ocs_get(f"/apps/circles/circles/{circle_id}/members")
        if members_data and "ocs" in members_data and "data" in members_data["ocs"]:
            for m in members_data["ocs"]["data"]:
                members.append({
                    "group": m.get("name") or m.get("displayname", ""),
                    "type": m.get("type"),
                    "level": m.get("level")
                })

        actual["collectives"][matched_group] = {"members": members}

    return actual


def diff_states(expected, actual):
    """Compare expected vs actual state and return a list of findings."""
    findings = []

    # Handle server unreachable case
    if not actual or not actual.get("group_folders") and not actual.get("calendars") and not actual.get("collectives"):
        return [{
            "category": "error",
            "resource": "server",
            "component": "connection",
            "path": "",
            "group": "N/A",
            "expected": "server reachable",
            "actual": "server unreachable or empty response",
            "status": "ERROR",
            "severity": "violation"
        }]

    # Group folders
    for group_name, expected_gf in expected.get("group_folders", {}).items():
        actual_gf = actual.get("group_folders", {}).get(group_name, {})

        # Membership
        expected_membership = expected_gf.get("membership", {})
        expected_absent = expected_gf.get("membership_absent", [])
        actual_membership = actual_gf.get("membership", {})

        for group, exp_perm in expected_membership.items():
            act_perm = actual_membership.get(group)
            if act_perm is None:
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "membership",
                    "path": "",
                    "group": group,
                    "expected": f"perm={exp_perm}",
                    "actual": "not found",
                    "status": "MISSING",
                    "severity": "warning"
                })
            elif act_perm != exp_perm:
                severity = "violation" if act_perm > exp_perm else "warning"
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "membership",
                    "path": "",
                    "group": group,
                    "expected": f"perm={exp_perm}",
                    "actual": f"perm={act_perm}",
                    "status": "DRIFT",
                    "severity": severity
                })

        for group in expected_absent:
            if group in actual_membership:
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "membership",
                    "path": "",
                    "group": group,
                    "expected": "absent",
                    "actual": f"perm={actual_membership[group]}",
                    "status": "EXCESS",
                    "severity": "violation"
                })

        for group, act_perm in actual_membership.items():
            if group not in expected_membership and group not in expected_absent:
                severity = "violation" if act_perm == 31 else "warning"
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "membership",
                    "path": "",
                    "group": group,
                    "expected": "absent",
                    "actual": f"perm={act_perm}",
                    "status": "EXCESS",
                    "severity": severity
                })

        # Root ACL
        expected_root_acl = expected_gf.get("root_acl", [])
        actual_root_acl = actual_gf.get("root_acl", [])
        actual_root_map = {a["group"]: a for a in actual_root_acl}

        for exp_acl in expected_root_acl:
            group = exp_acl["group"]
            exp_mask, exp_perm = exp_acl["mask"], exp_acl["permissions"]
            act_acl = actual_root_map.get(group)
            if not act_acl:
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "root_acl",
                    "path": "",
                    "group": group,
                    "expected": f"mask={exp_mask}, perm={exp_perm}",
                    "actual": "not found",
                    "status": "MISSING",
                    "severity": "warning"
                })
            elif act_acl["mask"] != exp_mask or act_acl["permissions"] != exp_perm:
                severity = "violation" if act_acl["permissions"] > exp_perm else "warning"
                findings.append({
                    "category": "group_folder",
                    "resource": group_name,
                    "component": "root_acl",
                    "path": "",
                    "group": group,
                    "expected": f"mask={exp_mask}, perm={exp_perm}",
                    "actual": f"mask={act_acl['mask']}, perm={act_acl['permissions']}",
                    "status": "DRIFT",
                    "severity": severity
                })

        # Subfolders
        for path, expected_sub in expected_gf.get("subfolders", {}).items():
            actual_sub = actual_gf.get("subfolders", {}).get(path, {})
            expected_acl = expected_sub.get("acl", [])
            actual_acl = actual_sub.get("acl", [])
            actual_acl_map = {a["group"]: a for a in actual_acl if a.get("type", "group") == "group"}

            # Expected ACL entries
            for exp_acl in expected_acl:
                group = exp_acl["group"]
                exp_mask, exp_perm = exp_acl["mask"], exp_acl["permissions"]
                act_acl = actual_acl_map.get(group)
                if not act_acl:
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "subfolder_acl",
                        "path": path,
                        "group": group,
                        "expected": f"mask={exp_mask}, perm={exp_perm}",
                        "actual": "not found",
                        "status": "MISSING",
                        "severity": "warning"
                    })
                elif act_acl["mask"] != exp_mask or act_acl["permissions"] != exp_perm:
                    severity = "violation" if act_acl["permissions"] > exp_perm else "warning"
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "subfolder_acl",
                        "path": path,
                        "group": group,
                        "expected": f"mask={exp_mask}, perm={exp_perm}",
                        "actual": f"mask={act_acl['mask']}, perm={act_acl['permissions']}",
                        "status": "DRIFT",
                        "severity": severity
                    })

            # Actual group ACL entries not in expected
            for act_acl in actual_acl:
                if act_acl.get("type", "group") != "group":
                    continue
                group = act_acl["group"]
                if group not in [e["group"] for e in expected_acl]:
                    severity = "violation" if act_acl["permissions"] > 1 else "warning"
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "subfolder_acl",
                        "path": path,
                        "group": group,
                        "expected": "absent",
                        "actual": f"mask={act_acl['mask']}, perm={act_acl['permissions']}",
                        "status": "EXCESS",
                        "severity": severity
                    })

            # Shares
            expected_shares = expected_sub.get("shares", [])
            actual_shares = actual_sub.get("shares", [])

            for exp_share in expected_shares:
                key = (exp_share["share_with"], exp_share["share_type"])
                exp_perm = exp_share["permissions"]
                act_share = next((s for s in actual_shares if s["share_with"] == key[0] and s["share_type"] == key[1]), None)
                if not act_share:
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "share",
                        "path": path,
                        "group": key[0],
                        "expected": f"perm={exp_perm}",
                        "actual": "not found",
                        "status": "MISSING",
                        "severity": "warning"
                    })
                elif act_share["permissions"] != exp_perm:
                    severity = "violation" if act_share["permissions"] > exp_perm else "warning"
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "share",
                        "path": path,
                        "group": key[0],
                        "expected": f"perm={exp_perm}",
                        "actual": f"perm={act_share['permissions']}",
                        "status": "DRIFT",
                        "severity": severity
                    })

            for act_share in actual_shares:
                key = (act_share["share_with"], act_share["share_type"])
                if not any(s["share_with"] == key[0] and s["share_type"] == key[1] for s in expected_shares):
                    severity = "violation" if act_share["permissions"] > 1 else "warning"
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "share",
                        "path": path,
                        "group": key[0],
                        "expected": "absent",
                        "actual": f"perm={act_share['permissions']}",
                        "status": "EXCESS",
                        "severity": severity
                    })

            # User ACLs (direct user permissions bypass group model)
            for act_acl in actual_acl:
                if act_acl.get("type") == "user":
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "user_acl",
                        "path": path,
                        "group": act_acl["group"],
                        "expected": "no direct user ACLs",
                        "actual": f"user={act_acl['group']}, mask={act_acl['mask']}, perm={act_acl['permissions']}",
                        "status": "EXCESS",
                        "severity": "warning"
                    })

            # User shares (share_type=0) — allowed but flagged for review
            for act_share in actual_shares:
                if act_share.get("share_type") == 0:
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "user_share",
                        "path": path,
                        "group": act_share.get("share_with", "unknown"),
                        "expected": "review required",
                        "actual": f"user share, perm={act_share['permissions']}",
                        "status": "EXCESS",
                        "severity": "warning"
                    })

            # Public links (share_type=3)
            for act_share in actual_shares:
                if act_share.get("share_type") == 3:
                    label = act_share.get("label", "public link")
                    if not act_share.get("has_password"):
                        findings.append({
                            "category": "group_folder",
                            "resource": group_name,
                            "component": "public_link",
                            "path": path,
                            "group": label,
                            "expected": "password required",
                            "actual": "no password",
                            "status": "EXCESS",
                            "severity": "violation"
                        })
                    elif not act_share.get("expiration"):
                        findings.append({
                            "category": "group_folder",
                            "resource": group_name,
                            "component": "public_link",
                            "path": path,
                            "group": label,
                            "expected": "expiration date set",
                            "actual": "no expiration",
                            "status": "EXCESS",
                            "severity": "warning"
                        })

        # Discovered subfolders not in YAML — check for user ACLs, public links, user shares
        actual_subfolders = actual_gf.get("subfolders", {})
        for path, actual_sub in actual_subfolders.items():
            if path in expected_gf.get("subfolders", {}):
                continue  # already checked above
            actual_acl = actual_sub.get("acl", [])
            actual_shares = actual_sub.get("shares", [])

            # User ACLs
            for act_acl in actual_acl:
                if act_acl.get("type") == "user":
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "user_acl",
                        "path": path,
                        "group": act_acl["group"],
                        "expected": "no direct user ACLs",
                        "actual": f"user={act_acl['group']}, mask={act_acl['mask']}, perm={act_acl['permissions']}",
                        "status": "EXCESS",
                        "severity": "warning"
                    })

            # User shares (share_type=0)
            for act_share in actual_shares:
                if act_share.get("share_type") == 0:
                    findings.append({
                        "category": "group_folder",
                        "resource": group_name,
                        "component": "user_share",
                        "path": path,
                        "group": act_share.get("share_with", "unknown"),
                        "expected": "review required",
                        "actual": f"user share, perm={act_share['permissions']}",
                        "status": "EXCESS",
                        "severity": "warning"
                    })

            # Public links (share_type=3)
            for act_share in actual_shares:
                if act_share.get("share_type") == 3:
                    label = act_share.get("label", "public link")
                    if not act_share.get("has_password"):
                        findings.append({
                            "category": "group_folder",
                            "resource": group_name,
                            "component": "public_link",
                            "path": path,
                            "group": label,
                            "expected": "password required",
                            "actual": "no password",
                            "status": "EXCESS",
                            "severity": "violation"
                        })
                    elif not act_share.get("expiration"):
                        findings.append({
                            "category": "group_folder",
                            "resource": group_name,
                            "component": "public_link",
                            "path": path,
                            "group": label,
                            "expected": "expiration date set",
                            "actual": "no expiration",
                            "status": "EXCESS",
                            "severity": "warning"
                        })

    # Calendars
    for cal_name, expected_cal in expected.get("calendars", {}).items():
        actual_cal = actual.get("calendars", {}).get(cal_name, {})
        expected_shares = expected_cal.get("shares", [])
        actual_shares = actual_cal.get("shares", [])

        for exp_share in expected_shares:
            group = exp_share["group"]
            exp_writable = exp_share["writable"]
            act_share = next((s for s in actual_shares if s["group"] == group), None)
            if not act_share:
                findings.append({
                    "category": "calendar",
                    "resource": cal_name,
                    "component": "calendar_share",
                    "path": "",
                    "group": group,
                    "expected": f"writable={exp_writable}",
                    "actual": "not found",
                    "status": "MISSING",
                    "severity": "warning"
                })
            elif act_share["writable"] != exp_writable:
                severity = "violation" if act_share["writable"] and not exp_writable else "warning"
                findings.append({
                    "category": "calendar",
                    "resource": cal_name,
                    "component": "calendar_share",
                    "path": "",
                    "group": group,
                    "expected": f"writable={exp_writable}",
                    "actual": f"writable={act_share['writable']}",
                    "status": "DRIFT",
                    "severity": severity
                })

        for act_share in actual_shares:
            group = act_share["group"]
            if not any(s["group"] == group for s in expected_shares):
                findings.append({
                    "category": "calendar",
                    "resource": cal_name,
                    "component": "calendar_share",
                    "path": "",
                    "group": group,
                    "expected": "absent",
                    "actual": f"writable={act_share['writable']}",
                    "status": "EXCESS",
                    "severity": "warning"
                })

    # Collectives (skip if app is disabled/unavailable)
    if actual.get("collectives"):
        for coll_name, expected_coll in expected.get("collectives", {}).items():
            actual_coll = actual.get("collectives", {}).get(coll_name, {})
            expected_members = expected_coll.get("members", [])
            actual_members = actual_coll.get("members", [])

            for exp_mem in expected_members:
                group = exp_mem["group"]
                exp_level = exp_mem["level"]
                act_mem = next((m for m in actual_members if m["group"] == group), None)
                if not act_mem:
                    findings.append({
                        "category": "collective",
                        "resource": coll_name,
                        "component": "collective_member",
                        "path": "",
                        "group": group,
                        "expected": f"level={exp_level}",
                        "actual": "not found",
                        "status": "MISSING",
                        "severity": "warning"
                    })
                elif act_mem["level"] != exp_level:
                    severity = "violation" if act_mem["level"] > exp_level else "warning"
                    findings.append({
                        "category": "collective",
                        "resource": coll_name,
                        "component": "collective_member",
                        "path": "",
                        "group": group,
                        "expected": f"level={exp_level}",
                        "actual": f"level={act_mem['level']}",
                        "status": "DRIFT",
                        "severity": severity
                    })

            for act_mem in actual_members:
                group = act_mem["group"]
                if not any(m["group"] == group for m in expected_members):
                    findings.append({
                        "category": "collective",
                        "resource": coll_name,
                        "component": "collective_member",
                        "path": "",
                        "group": group,
                        "expected": "absent",
                        "actual": f"level={act_mem['level']}",
                        "status": "EXCESS",
                        "severity": "warning"
                    })

    return findings


def generate_report(findings, configs_used):
    """Generate markdown compliance report from findings."""
    if not findings:
        return "# Rights Compliance Report\n\n**Status:** PASS | **Generated:** N/A | **Configs:** N/A\n\nNo findings."

    # Count by status
    counts = {"OK": 0, "DRIFT": 0, "MISSING": 0, "EXCESS": 0, "ERROR": 0}
    violations = 0
    warnings = 0
    for f in findings:
        status = f.get("status", "OK")
        severity = f.get("severity", "ok")
        counts[status] = counts.get(status, 0) + 1
        if severity == "violation" or status == "ERROR":
            violations += 1
        elif severity == "warning":
            warnings += 1

    status = "FAIL" if violations > 0 else "PASS"
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    config_names = ", ".join(os.path.basename(c) for c in configs_used)

    lines = [
        f"# Rights Compliance Report",
        f"",
        f"**Status:** {status} | **Generated:** {timestamp} | **Configs:** {config_names}",
        f"",
        f"## Summary",
        f"",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| OK | {counts.get('OK', 0)} |",
        f"| DRIFT | {counts.get('DRIFT', 0)} |",
        f"| MISSING | {counts.get('MISSING', 0)} |",
        f"| EXCESS | {counts.get('EXCESS', 0)} |",
        f"| **Violations** | **{violations}** |",
        f"| Warnings | {warnings} |",
        f"",
    ]

    # Group findings by category and resource
    by_category = {}
    for f in findings:
        cat = f.get("category", "other")
        res = f.get("resource", "unknown")
        key = f"{cat}:{res}"
        if key not in by_category:
            by_category[key] = []
        by_category[key].append(f)

    # Only show sections with non-OK findings
    for key, group_finds in by_category.items():
        cat, res = key.split(":", 1)
        has_non_ok = any(f.get("status") != "OK" for f in group_finds)
        if not has_non_ok:
            continue

        if cat == "group_folder":
            lines.append(f"### Group Folder: {res}")
            # Membership
            mem_finds = [f for f in group_finds if f.get("component") == "membership"]
            if mem_finds:
                lines.append(f"#### Membership")
                lines.append(f"| Group | Expected | Actual | Status | Severity |")
                lines.append(f"|-------|----------|--------|--------|----------|")
                for f in mem_finds:
                    lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                lines.append("")

            # Root ACL
            root_finds = [f for f in group_finds if f.get("component") == "root_acl"]
            if root_finds:
                lines.append(f"#### Root ACL")
                lines.append(f"| Group | Expected | Actual | Status | Severity |")
                lines.append(f"|-------|----------|--------|--------|----------|")
                for f in root_finds:
                    lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                lines.append("")

            # Subfolders
            sub_paths = set(f.get("path") for f in group_finds if f.get("component") in ("subfolder_acl", "share", "user_acl", "user_share", "public_link") and f.get("path"))
            for path in sub_paths:
                lines.append(f"#### Subfolder: {path}")
                # ACLs
                acl_finds = [f for f in group_finds if f.get("component") == "subfolder_acl" and f.get("path") == path]
                if acl_finds:
                    lines.append(f"**ACLs:**")
                    lines.append(f"| Group | Expected | Actual | Status | Severity |")
                    lines.append(f"|-------|----------|--------|--------|----------|")
                    for f in acl_finds:
                        lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                    lines.append("")
                # Shares
                share_finds = [f for f in group_finds if f.get("component") == "share" and f.get("path") == path]
                if share_finds:
                    lines.append(f"**Shares:**")
                    lines.append(f"| Recipient | Expected | Actual | Status | Severity |")
                    lines.append(f"|-----------|----------|--------|--------|----------|")
                    for f in share_finds:
                        lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                    lines.append("")
                # User ACLs
                user_acl_finds = [f for f in group_finds if f.get("component") == "user_acl" and f.get("path") == path]
                if user_acl_finds:
                    lines.append(f"**User ACLs (review):**")
                    lines.append(f"| User | Expected | Actual | Status | Severity |")
                    lines.append(f"|------|----------|--------|--------|----------|")
                    for f in user_acl_finds:
                        lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                    lines.append("")
                # User shares
                user_share_finds = [f for f in group_finds if f.get("component") == "user_share" and f.get("path") == path]
                if user_share_finds:
                    lines.append(f"**User Shares (review):**")
                    lines.append(f"| User | Expected | Actual | Status | Severity |")
                    lines.append(f"|------|----------|--------|--------|----------|")
                    for f in user_share_finds:
                        lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                    lines.append("")
                # Public links
                pub_link_finds = [f for f in group_finds if f.get("component") == "public_link" and f.get("path") == path]
                if pub_link_finds:
                    lines.append(f"**Public Links:**")
                    lines.append(f"| Label | Expected | Actual | Status | Severity |")
                    lines.append(f"|-------|----------|--------|--------|----------|")
                    for f in pub_link_finds:
                        lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
                    lines.append("")

        elif cat == "calendar":
            lines.append(f"### Calendar: {res}")
            lines.append(f"| Group | Expected | Actual | Status | Severity |")
            lines.append(f"|-------|----------|--------|--------|----------|")
            for f in group_finds:
                lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
            lines.append("")

        elif cat == "collective":
            lines.append(f"### Collective: {res}")
            lines.append(f"| Group | Expected Level | Actual Level | Status | Severity |")
            lines.append(f"|-------|---------------|-------------|--------|----------|")
            for f in group_finds:
                lines.append(f"| {f['group']} | {f['expected']} | {f['actual']} | {f['status']} | {f['severity']} |")
            lines.append("")

        elif cat == "error":
            lines.append(f"### Error")
            for f in group_finds:
                lines.append(f"- **{f['actual']}**: {f['expected']}")
            lines.append("")

    return "\n".join(lines)


def upload_report(markdown):
    """Upload markdown report to Nextcloud via WebDAV."""
    if not NC_STATS_DIR:
        print("  Warning: NC_STATS_DIR not set, skipping upload")
        return

    parent_dir = "/".join(NC_STATS_DIR.rstrip("/").split("/")[:-1])
    webdav_url = f"{NC_URL}/remote.php/dav/files/{NC_ANCHOR_USER}/{parent_dir}/compliance_report.md"

    try:
        resp = requests.put(
            webdav_url,
            data=markdown,
            auth=HTTPBasicAuth(NC_ANCHOR_USER, NC_ANCHOR_APP_PW),
            headers={"Content-Type": "text/markdown"},
            timeout=30
        )
        resp.raise_for_status()
        print(f"  Report uploaded to {webdav_url}")
    except Exception as e:
        print(f"  Warning: Failed to upload report: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Nextcloud Rights Compliance Checker"
    )
    parser.add_argument(
        "configs",
        nargs="*",
        help="YAML config file paths"
    )
    parser.add_argument(
        "--config-dir",
        help="Directory to glob *.yaml/*.yml from"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print expected state JSON and exit (no server contact)"
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload report to Nextcloud after generation"
    )

    args = parser.parse_args()

    # Collect config files
    file_paths = list(args.configs)
    if args.config_dir:
        file_paths.extend(glob_configs(args.config_dir))

    if not file_paths:
        parser.error("No config files specified")

    # Load and merge configs
    config = load_config_files(file_paths)

    # Build expected state
    expected = build_expected_state(config)

    if args.dry_run:
        print(json.dumps(expected, indent=2))
        sys.exit(0)

    # Full compliance check — enrich expected state with all server group folders
    expected = enrich_expected_from_server(expected)
    actual = fetch_actual_state(expected)
    findings = diff_states(expected, actual)
    report = generate_report(findings, file_paths)

    # Save reports
    os.makedirs('reports', exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = f"reports/compliance_check_{timestamp}.json"
    md_path = f"reports/compliance_check_{timestamp}.md"

    with open(json_path, 'w') as f:
        json.dump(findings, f, indent=2)
    print(f"JSON report saved to {json_path}")

    with open(md_path, 'w') as f:
        f.write(report)
    print(f"Markdown report saved to {md_path}")

    # Count violations and warnings
    violations = sum(1 for f in findings if f.get("severity") == "violation" or f.get("status") == "ERROR")
    warnings = sum(1 for f in findings if f.get("severity") == "warning")

    print(f"\nSummary: {violations} violation(s), {warnings} warning(s)")

    if args.upload:
        upload_report(report)

    sys.exit(1 if violations > 0 else 0)


if __name__ == "__main__":
    main()