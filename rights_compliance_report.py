import os
import json
import datetime
import requests
import caldav
from requests.auth import HTTPBasicAuth
from xml.etree import ElementTree as ET

NEXTCLOUD_URL = os.getenv("NC_URL")
ANCHOR_USER   = os.getenv("NC_ANCHOR_USER")
ANCHOR_APP_PW = os.getenv("NC_ANCHOR_APP_PW")
ALL_MEMBERS_GROUP = os.getenv("NC_ALL_MEMBERS_GROUP", "all_users")
ADMIN_GROUP   = os.getenv("NC_ADMIN_GROUP", "admin")

# Subfolder naming configuration
NC_PUB_FOLDER_PREFIX = os.getenv("NC_PUB_FOLDER_PREFIX", "01")
NC_PRIV_FOLDER_PREFIX = os.getenv("NC_PRIV_FOLDER_PREFIX", "02")
NC_PUBLIC_SUBFOLDER = os.getenv("NC_PUBLIC_SUBFOLDER", "Public")
NC_SUBFOLDERS = os.getenv("NC_SUBFOLDERS", "Public,Privat,Archive").split(",")

auth = HTTPBasicAuth(ANCHOR_USER, ANCHOR_APP_PW)
ocs_headers = {"OCS-APIRequest": "true", "Accept": "application/json"}

# ── helpers ──────────────────────────────────────────────────────────────────

def ocs_get(path, params=None):
    r = requests.get(f"{NEXTCLOUD_URL}{path}", auth=auth, headers=ocs_headers, params=params)
    r.raise_for_status()
    return r.json()

def is_all_members_group(name):
    return name == ALL_MEMBERS_GROUP

def perm_label(p):
    p = int(p)
    if p == 0:  return "none(0)"
    if p == 1:  return "read(1)"
    if p == 31: return "full(31)"
    return f"custom({p})"

# ── 1. Group Folders ──────────────────────────────────────────────────────────

def get_subfolder_permissions(folder_id):
    """Get ACL permissions for subfolders of a group folder via WebDAV."""
    try:
        # Get folder info to find mount point
        resp = ocs_get(f"/apps/groupfolders/folders/{folder_id}")
        folder_data = resp.get("ocs", {}).get("data", {})
        mount_point = folder_data.get("mount_point", "")
        
        if not mount_point:
            return []
        
        # Use WebDAV to list subfolders and their ACLs
        webdav_url = f"{NEXTCLOUD_URL}/remote.php/dav/files/{ANCHOR_USER}/{mount_point}"
        propfind = '''<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">
  <d:prop>
    <d:resourcetype/>
    <d:displayname/>
    <nc:acl-list/>
  </d:prop>
</d:propfind>'''
        
        r = requests.request(
            "PROPFIND", webdav_url, auth=auth,
            headers={"Content-Type": "application/xml", "Depth": "1"},
            data=propfind
        )
        
        from xml.etree import ElementTree as ET
        ns = {"d": "DAV:", "nc": "http://nextcloud.org/ns"}
        root = ET.fromstring(r.text)
        
        subfolders = []
        for response in root.findall(".//d:response", ns):
            href = response.find("d:href", ns)
            if href is None:
                continue
            href_text = href.text.rstrip("/")
            # Extract subfolder name from href
            parts = href_text.split("/")
            if len(parts) < 5:  # /remote.php/dav/files/user/folder/subfolder
                continue
            subfolder_name = parts[-1]
            
            # Skip the main folder itself (it has no parent folder in path)
            if len(parts) == 5:  # This is the main folder, not a subfolder
                continue
            
            # Get ACL list
            acl_list = response.find(".//nc:acl-list", ns)
            if acl_list is not None:
                # Parse ACL rules - structure is nc:acl with nc:acl-mapping-id and nc:acl-permissions
                groups = {}
                for acl in acl_list.findall("nc:acl", ns):
                    mapping_id = acl.find("nc:acl-mapping-id", ns)
                    perm_elem = acl.find("nc:acl-permissions", ns)
                    if mapping_id is not None and perm_elem is not None:
                        groups[mapping_id.text] = int(perm_elem.text)
                
                if groups:
                    subfolders.append({
                        "mount_point": subfolder_name,
                        "groups": groups
                    })
        
        return subfolders
    except Exception as e:
        return []

def audit_group_folders():
    findings = []
    data = ocs_get("/apps/groupfolders/folders")
    folders = data.get("ocs", {}).get("data", {})
    if isinstance(folders, dict):
        folders = list(folders.values())

    for f in folders:
        fid   = f["id"]
        name  = f["mount_point"]
        groups = f.get("groups", {})  # {group_name: perm_int}
        acl_enabled = f.get("acl", False)

        folder_findings = []

        # Expected: only ADMIN_GROUP with 31; all other groups managed via ACL
        for grp, perm in groups.items():
            perm = int(perm)
            if grp == ADMIN_GROUP:
                status = "ok" if perm == 31 else "warning"
            elif is_all_members_group(grp):
                status = "violation" if perm > 1 else "ok"
            else:
                # All other groups are WG groups - should have 31 (ACL restricts further)
                status = "ok" if perm == 31 else "warning"

            folder_findings.append({
                "group": grp,
                "permission": perm,
                "permission_label": perm_label(perm),
                "status": status,
                "note": "" if status == "ok" else f"Unexpected permission {perm} for {grp}"
            })

        if not acl_enabled:
            folder_findings.append({
                "group": "—",
                "permission": None,
                "permission_label": "—",
                "status": "warning",
                "note": "ACL not enabled on folder"
            })

        # Audit subfolders
        subfolders = get_subfolder_permissions(fid)
        subfolder_findings = []
        for sub in subfolders:
            sub_name = sub.get("mount_point", "")
            sub_groups = sub.get("groups", {})
            
            # Check subfolder permissions
            for grp, perm in sub_groups.items():
                perm = int(perm)
                # Public folder (01_*) - everyone should have read(1), WG should have full(31)
                if sub_name.startswith(f"{NC_PUB_FOLDER_PREFIX}_") and NC_PUBLIC_SUBFOLDER in sub_name:
                    if is_all_members_group(grp):
                        status = "ok" if perm == 1 else "violation"
                        note = "" if perm == 1 else f"Public folder: {grp} should have read(1), got {perm}"
                    elif grp == ADMIN_GROUP:
                        status = "ok" if perm == 31 else "warning"
                        note = ""
                    else:
                        # WG group should have full access
                        status = "ok" if perm == 31 else "violation"
                        note = "" if perm == 31 else f"Public folder: {grp} should have full(31), got {perm}"
                # Private/Archive folder (02_*) - everyone should have none(0), WG should have full(31)
                elif sub_name.startswith(f"{NC_PRIV_FOLDER_PREFIX}_"):
                    if is_all_members_group(grp):
                        status = "ok" if perm == 0 else "violation"
                        note = "" if perm == 0 else f"Private/Archive: {grp} should have none(0), got {perm}"
                    elif grp == ADMIN_GROUP:
                        status = "ok" if perm == 31 else "warning"
                        note = ""
                    else:
                        # WG group should have full access
                        status = "ok" if perm == 31 else "violation"
                        note = "" if perm == 31 else f"Private/Archive: {grp} should have full(31), got {perm}"
                else:
                    status = "info"
                    note = f"Unknown subfolder type: {sub_name}"
                
                subfolder_findings.append({
                    "subfolder": sub_name,
                    "group": grp,
                    "permission": perm,
                    "permission_label": perm_label(perm),
                    "status": status,
                    "note": note
                })

        findings.append({
            "folder_id": fid,
            "folder_name": name,
            "acl_enabled": acl_enabled,
            "groups": folder_findings,
            "subfolders": subfolder_findings,
            "shares": get_folder_shares(fid)
        })

    return findings

# ── 2. Calendars ──────────────────────────────────────────────────────────────

def audit_calendars():
    findings = []
    try:
        client = caldav.DAVClient(
            f"{NEXTCLOUD_URL}/remote.php/dav",
            username=ANCHOR_USER, password=ANCHOR_APP_PW
        )
        calendars = client.principal().calendars()
    except Exception as e:
        return [{"error": str(e)}]

    # Fetch OCS shares for each calendar path
    shares_resp = ocs_get("/ocs/v2.php/apps/files_sharing/api/v1/shares", {"shared_with_me": "false"})
    all_shares = shares_resp.get("ocs", {}).get("data", [])

    for cal in calendars:
        cal_name = cal.get_display_name()
        cal_path = str(cal.url).split(f"/remote.php/dav/calendars/{ANCHOR_USER}/")[-1].rstrip("/")

        # Find DAV shares via PROPFIND
        share_info = get_calendar_shares(cal.url)

        cal_findings = []
        for share in share_info:
            principal = share.get("href", "")
            writable  = share.get("writable", False)
            group_name = principal.split("/")[-1] if principal else "unknown"

            if is_all_members_group(group_name):
                status = "ok" if not writable else "violation"
                note   = "" if not writable else "All-members group should be read-only"
            else:
                status = "ok"  # All other groups are WG groups with write access
                note   = ""

            cal_findings.append({
                "recipient": group_name,
                "writable": writable,
                "status": status,
                "note": note
            })

        findings.append({
            "calendar_name": cal_name,
            "calendar_id": cal_path,
            "shares": cal_findings
        })

    return findings


def get_all_shares():
    """Get all shares from the server."""
    try:
        shares_resp = ocs_get("/ocs/v2.php/apps/files_sharing/api/v1/shares", {
            "shared_with_me": "false",
            "reshared": "true"
        })
        shares = shares_resp.get("ocs", {}).get("data", [])
        
        share_info = []
        for share in shares:
            share_type = share.get("share_type", "")
            if share_type == 3:  # Public link
                share_info.append({
                    "type": "public_link",
                    "name": share.get("name", "unknown"),
                    "path": share.get("path", ""),
                    "password_protected": share.get("share_with", "") != "",
                    "dangerous": share.get("share_with", "") == "",
                    "status": "dangerous" if share.get("share_with", "") == "" else "ok",
                    "note": "Public link without password protection" if share.get("share_with", "") == "" else ""
                })
            elif share_type in [0, 1]:  # Internal share (user/group)
                share_info.append({
                    "type": "internal",
                    "name": share.get("name", "unknown"),
                    "path": share.get("path", ""),
                    "recipient": share.get("share_with", ""),
                    "status": "ok",
                    "note": ""
                })
        
        return share_info
    except Exception:
        return []


def get_folder_shares(folder_id):
    """Get shares for a group folder via OCS API."""
    try:
        # Get folder mount point
        resp = ocs_get(f"/apps/groupfolders/folders/{folder_id}")
        mount_point = resp.get("ocs", {}).get("data", {}).get("mount_point", "")
        
        if not mount_point:
            return []
        
        # Get shares for this folder path
        shares_resp = ocs_get("/ocs/v2.php/apps/files_sharing/api/v1/shares", {
            "path": f"/{ANCHOR_USER}/{mount_point}",
            "shared_with_me": "false"
        })
        shares = shares_resp.get("ocs", {}).get("data", [])
        
        share_info = []
        for share in shares:
            share_type = share.get("share_type", "")
            if share_type == 1:  # User share
                recipient = share.get("share_with", "")
            elif share_type == 2:  # Group share
                recipient = share.get("share_with", "")
            elif share_type == 6:  # Public link
                recipient = "public_link"
            else:
                recipient = share.get("share_with", "unknown")
            
            share_info.append({
                "recipient": recipient,
                "share_type": share_type,
                "writable": share.get("permissions", 0) & 4 > 0,
                "status": "ok",
                "note": ""
            })
        
        return share_info
    except Exception:
        return []


def get_calendar_shares(cal_url):
    """PROPFIND to retrieve share-with info from a calendar."""
    propfind_xml = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:o="http://owncloud.org/ns">
  <d:prop>
    <o:invite/>
  </d:prop>
</d:propfind>"""
    try:
        r = requests.request(
            "PROPFIND", str(cal_url), auth=auth,
            headers={"Content-Type": "application/xml", "Depth": "0"},
            data=propfind_xml
        )
        ns = {
            "d": "DAV:",
            "o": "http://owncloud.org/ns",
            "cs": "http://calendarserver.org/ns/"
        }
        root = ET.fromstring(r.text)
        shares = []
        for user in root.findall(".//o:invite/o:user", ns):
            href_el = user.find("d:href", ns)
            access_el = user.find("o:access", ns)
            href = href_el.text if href_el is not None else ""
            writable = access_el.find("o:read-write", ns) is not None if access_el is not None else False
            shares.append({"href": href, "writable": writable})
        return shares
    except Exception:
        return []

# ── 3. Collectives / Circles ──────────────────────────────────────────────────

CIRCLE_LEVEL = {1: "Member", 4: "Moderator", 8: "Admin", 9: "Owner"}

def audit_collectives():
    findings = []
    try:
        resp = ocs_get("/ocs/v2.php/apps/collectives/api/v1.0/collectives")
        collectives = resp.get("ocs", {}).get("data", {}).get("collectives", [])
    except Exception as e:
        return [{"error": str(e)}]

    for col in collectives:
        col_name  = col.get("name", "")
        circle_id = col.get("circleId", "")
        edit_level = col.get("editPermissionLevel", None)

        member_findings = []
        try:
            m_resp = ocs_get(f"/ocs/v2.php/apps/circles/circles/{circle_id}/members")
            members = m_resp.get("ocs", {}).get("data", [])
        except Exception:
            members = []

        for m in members:
            m_name  = m.get("displayName") or m.get("userId", "")
            m_type  = m.get("userType", 0)   # 1=user, 2=group
            m_level = m.get("level", 1)
            level_label = CIRCLE_LEVEL.get(m_level, f"level{m_level}")
            type_label  = "group" if m_type == 2 else "user"

            if type_label == "group":
                if is_all_members_group(m_name):
                    status = "ok" if m_level == 1 else "violation"
                    note   = "" if m_level == 1 else f"All-members group should be Member(1), got {level_label}"
                else:
                    status = "ok"  # All other groups are WG groups with Moderator(4) access
                    note   = ""
            else:
                status = "ok"
                note   = ""

            member_findings.append({
                "name": m_name,
                "type": type_label,
                "level": m_level,
                "level_label": level_label,
                "status": status,
                "note": note
            })

        findings.append({
            "collective_name": col_name,
            "circle_id": circle_id,
            "edit_level": edit_level,
            "members": member_findings
        })

    return findings

# ── 4. Users in multiple groups ─────────────────────────────────────────────

def audit_multi_wg_users():
    findings = []
    try:
        resp = ocs_get("/ocs/v1.php/cloud/groups")
        all_groups = resp.get("ocs", {}).get("data", {}).get("groups", [])
    except Exception as e:
        return [{"error": str(e)}]

    # All groups except ALL_MEMBERS_GROUP are WG groups
    wg_groups = [g for g in all_groups if g != ALL_MEMBERS_GROUP]
    user_wg_map = {}  # user -> [wg_groups]

    for grp in wg_groups:
        try:
            r = ocs_get(f"/ocs/v1.php/cloud/groups/{grp}")
            members = r.get("ocs", {}).get("data", {}).get("users", [])
        except Exception:
            members = []
        for u in members:
            user_wg_map.setdefault(u, []).append(grp)

    for user, groups in user_wg_map.items():
        if len(groups) > 1:
            findings.append({
                "user": user,
                "wg_groups": groups,
                "count": len(groups),
                "status": "warning",
                "note": f"Member of {len(groups)} groups: {', '.join(groups)}"
            })

    return findings

# ── Report assembly ───────────────────────────────────────────────────────────

def overall_status(sections):
    statuses = []
    def collect(obj):
        if isinstance(obj, dict):
            if "status" in obj:
                statuses.append(obj["status"])
            for v in obj.values():
                collect(v)
        elif isinstance(obj, list):
            for i in obj:
                collect(i)
    collect(sections)
    if "violation" in statuses: return "violation"
    if "warning"   in statuses: return "warning"
    return "ok"

STATUS_COLOR = {"ok": "#2ecc71", "warning": "#f39c12", "violation": "#e74c3c"}
STATUS_ICON  = {"ok": "✅", "warning": "⚠️", "violation": "❌"}

def badge(status):
    c = STATUS_COLOR.get(status, "#999")
    return f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:4px;font-size:.85em">{status.upper()}</span>'

def build_markdown(report):
    ts = report["generated_at"]
    s  = report["summary"]

    # ── group folders ──
    gf_rows = ""
    for f in report["group_folders"]:
        if "error" in f:
            gf_rows += f"| {f['error']} |\n"
            continue
        acl = "✅" if f["acl_enabled"] else "⚠️"
        for g in f["groups"]:
            gf_rows += f"| {f['folder_name']} | {acl} | {g['group']} | {g['permission_label']} | {g['status'].upper()} | {g['note']} |\n"
        if not f["groups"]:
            gf_rows += f"| {f['folder_name']} | {acl} | - | - | - | - |\n"
        # Add subfolder findings
        for sub in f.get("subfolders", []):
            sub_status = "SUBFOLD" if sub["status"] == "info" else sub["status"].upper()
            gf_rows += f"| {f['folder_name']}/{sub['subfolder']} | - | {sub['group']} | {sub['permission_label']} | {sub_status} | {sub['note']} |\n"
        for sh in f.get("shares", []):
            access = "write" if sh["writable"] else "read"
            gf_rows += f"| {f['folder_name']} | - | {sh['recipient']} | {access} | {sh['status'].upper()} | {sh['note']} |\n"

    # ── calendars ──
    cal_rows = ""
    for c in report["calendars"]:
        if "error" in c:
            cal_rows += f"| {c['error']} |\n"
            continue
        for sh in c["shares"]:
            access = "write" if sh["writable"] else "read"
            cal_rows += f"| {c['calendar_name']} | {sh['recipient']} | {access} | {sh['status'].upper()} | {sh['note']} |\n"
        if not c["shares"]:
            cal_rows += f"| {c['calendar_name']} | - | - | - | - |\n"

    # ── collectives ──
    col_rows = ""
    for c in report["collectives"]:
        if "error" in c:
            col_rows += f"| {c['error']} |\n"
            continue
        for m in c["members"]:
            col_rows += f"| {c['collective_name']} | {m['name']} | {m['type']} | {m['level_label']} | {m['status'].upper()} | {m['note']} |\n"
        if not c["members"]:
            col_rows += f"| {c['collective_name']} | - | - | - | - | - |\n"

    # ── talk ──
    talk_rows = ""

    # ── multi-wg ──
    mwg_rows = ""
    for u in report["multi_wg_users"]:
        if "error" in u:
            mwg_rows += f"| {u['error']} |\n"
            continue
        wg_list = ", ".join(u["wg_groups"])
        mwg_rows += f"| {u['user']} | {wg_list} | {u['status'].upper()} | {u['note']} |\n"
    if not report["multi_wg_users"]:
        mwg_rows = "| - | - | - | No users in multiple WG groups |\n"

    # ── all shares ──
    shares_rows = ""
    for sh in report.get("all_shares", []):
        status = "⚠️ DANGEROUS" if sh.get("dangerous") else sh["status"].upper()
        shares_rows += f"| {sh['type']} | {sh['name']} | {sh['path']} | {status} | {sh['note']} |\n"
    if not report.get("all_shares"):
        shares_rows = "| - | - | - | - | No shares found |\n"

    overall = overall_status(report)

    return f"""# Nextcloud Rights & Compliance Report

**Status:** {overall} | **Generated:** {ts} | **Server:** {NEXTCLOUD_URL}

## Summary

| Category | Total | Violations | Warnings |
|----------|-------|------------|----------|
| Group Folders | {s["group_folders_total"]} | {s["group_folders_violations"]} | {s["group_folders_warnings"]} |
| Calendars | {s["calendars_total"]} | {s["calendars_violations"]} | {s["calendars_warnings"]} |
| Collectives | {s["collectives_total"]} | {s["collectives_violations"]} | {s["collectives_warnings"]} |
| Talk Rooms | {s["talk_rooms_total"]} | {s["talk_rooms_violations"]} | {s["talk_rooms_warnings"]} |
| Multi-WG Users | - | - | {s["multi_wg_users"]} |
| Internal Shares | {s["internal_shares"]} | - | - |
| Public Shares | {s["public_shares"]} | {s["dangerous_shares"]} | - |

---

## Group Folder Permissions

| Folder | ACL | Group | Permission | Status | Note |
|--------|-----|-------|------------|--------|------|
{gf_rows}

---

## Calendar Shares

| Calendar | Recipient | Access | Status | Note |
|----------|-----------|--------|--------|------|
{cal_rows}

---

## Collective / Circle Membership

| Collective | Member | Type | Level | Status | Note |
|------------|--------|------|-------|--------|------|
{col_rows}

---

## Users in Multiple WG Groups

| User | WG Groups | Status | Note |
|------|-----------|--------|------|
{mwg_rows}

---

## All Shares

| Type | Name | Path | Status | Note |
|------|------|------|--------|------|
{shares_rows}
"""


def build_html(report):
    ts = report["generated_at"]
    s  = report["summary"]

    def section_header(title, status):
        return f'<h2>{STATUS_ICON.get(status,"")} {title} {badge(status)}</h2>'

    # ── group folders ──
    gf_rows = ""
    for f in report["group_folders"]:
        if "error" in f:
            gf_rows += f"| {f['error']} |\n"
            continue
        acl = "✅" if f["acl_enabled"] else "⚠️"
        for g in f["groups"]:
            gf_rows += f"| {f['folder_name']} | {acl} | {g['group']} | {g['permission_label']} | {g['status'].upper()} | {g['note']} |\n"
        if not f["groups"]:
            gf_rows += f"| {f['folder_name']} | {acl} | - | - | - | - |\n"
        # Add subfolder findings
        for sub in f.get("subfolders", []):
            sub_status = "SUBFOLD" if sub["status"] == "info" else sub["status"].upper()
            gf_rows += f"| {f['folder_name']}/{sub['subfolder']} | - | {sub['group']} | {sub['permission_label']} | {sub_status} | {sub['note']} |\n"
        for sh in f.get("shares", []):
            access = "write" if sh["writable"] else "read"
            gf_rows += f"| {f['folder_name']} | - | {sh['recipient']} | {access} | {sh['status'].upper()} | {sh['note']} |\n"

    # ── calendars ──
    cal_rows = ""
    for c in report["calendars"]:
        if "error" in c:
            cal_rows += f"| {c['error']} |\n"
            continue
        for sh in c["shares"]:
            access = "write" if sh["writable"] else "read"
            cal_rows += f"| {c['calendar_name']} | {sh['recipient']} | {access} | {sh['status'].upper()} | {sh['note']} |\n"
        if not c["shares"]:
            cal_rows += f"| {c['calendar_name']} | - | - | - | - |\n"

    # ── collectives ──
    col_rows = ""
    for c in report["collectives"]:
        if "error" in c:
            col_rows += f"| {c['error']} |\n"
            continue
        for m in c["members"]:
            col_rows += f"| {c['collective_name']} | {m['name']} | {m['type']} | {m['level_label']} | {m['status'].upper()} | {m['note']} |\n"
        if not c["members"]:
            col_rows += f"| {c['collective_name']} | - | - | - | - | - |\n"

    # ── talk ──
    talk_rows = ""

    # ── multi-wg ──
    mwg_rows = ""
    for u in report["multi_wg_users"]:
        if "error" in u:
            mwg_rows += f"| {u['error']} |\n"
            continue
        wg_list = ", ".join(u["wg_groups"])
        mwg_rows += f"| {u['user']} | {wg_list} | {u['status'].upper()} | {u['note']} |\n"
    if not report["multi_wg_users"]:
        mwg_rows = "| - | - | - | No users in multiple WG groups |\n"

    # ── all shares ──
    shares_rows = ""
    for sh in report.get("all_shares", []):
        status = "⚠️ DANGEROUS" if sh.get("dangerous") else sh["status"].upper()
        shares_rows += f"| {sh['type']} | {sh['name']} | {sh['path']} | {status} | {sh['note']} |\n"
    if not report.get("all_shares"):
        shares_rows = "| - | - | - | - | No shares found |\n"

    overall = overall_status(report)

    return f"""# Nextcloud Rights & Compliance Report

**Status:** {overall_status(report["group_folders"])} | **Generated:** {ts} | **Server:** {NEXTCLOUD_URL}

## Summary

| Category | Total | Violations | Warnings |
|----------|-------|------------|----------|
| Group Folders | {s["group_folders_total"]} | {s["group_folders_violations"]} | {s["group_folders_warnings"]} |
| Calendars | {s["calendars_total"]} | {s["calendars_violations"]} | {s["calendars_warnings"]} |
| Collectives | {s["collectives_total"]} | {s["collectives_violations"]} | {s["collectives_warnings"]} |
| Talk Rooms | {s["talk_rooms_total"]} | {s["talk_rooms_violations"]} | {s["talk_rooms_warnings"]} |
| Multi-WG Users | - | - | {s["multi_wg_users"]} |
| Internal Shares | {s["internal_shares"]} | - | - |
| Public Shares | {s["public_shares"]} | {s["dangerous_shares"]} | - |

---

## Group Folder Permissions

| Folder | ACL | Group | Permission | Status | Note |
|--------|-----|-------|------------|--------|------|
{gf_rows}

---

## Calendar Shares

| Calendar | Recipient | Access | Status | Note |
|----------|-----------|--------|--------|------|
{cal_rows}

---

## Collective / Circle Membership

| Collective | Member | Type | Level | Status | Note |
|------------|--------|------|-------|--------|------|
{col_rows}

---

## Users in Multiple WG Groups

| User | WG Groups | Status | Note |
|------|-----------|--------|------|
{mwg_rows}

---

## All Shares

| Type | Name | Path | Status | Note |
|------|------|------|--------|------|
{shares_rows}
"""


def count_statuses(items):
    violations = warnings = 0
    def walk(obj):
        nonlocal violations, warnings
        if isinstance(obj, dict):
            s = obj.get("status")
            if s == "violation": violations += 1
            elif s == "warning": warnings += 1
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for i in obj: walk(i)
    walk(items)
    return violations, warnings


def main():
    print("🔍 Auditing group folders...")
    gf = audit_group_folders()
    print("🔍 Auditing calendars...")
    cal = audit_calendars()
    print("🔍 Auditing collectives...")
    col = audit_collectives()
    print("🔍 Checking all shares...")
    all_shares = get_all_shares()
    print("🔍 Checking multi-WG users...")
    mwg = audit_multi_wg_users()

    gf_v,  gf_w  = count_statuses(gf)
    cal_v, cal_w = count_statuses(cal)
    col_v, col_w = count_statuses(col)
    
    # Count dangerous shares
    dangerous_shares = sum(1 for s in all_shares if s.get("dangerous", False))
    internal_shares = sum(1 for s in all_shares if s.get("type") == "internal")
    public_shares = sum(1 for s in all_shares if s.get("type") == "public_link")

    report = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "server": NEXTCLOUD_URL,
        "summary": {
            "group_folders_total":      len(gf),
            "group_folders_violations": gf_v,
            "group_folders_warnings":   gf_w,
            "calendars_total":          len(cal),
            "calendars_violations":     cal_v,
            "calendars_warnings":       cal_w,
            "collectives_total":        len(col),
            "collectives_violations":   col_v,
            "collectives_warnings":     col_w,
            "talk_rooms_total":         0,
            "talk_rooms_violations":    0,
            "talk_rooms_warnings":      0,
            "multi_wg_users":           len(mwg),
            "all_shares_total":         len(all_shares),
            "internal_shares":          internal_shares,
            "public_shares":            public_shares,
            "dangerous_shares":         dangerous_shares,
        },
        "group_folders": gf,
        "calendars":     cal,
        "collectives":   col,
        "talk_rooms":    [],
        "multi_wg_users": mwg,
        "all_shares":    all_shares,
    }

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("reports", exist_ok=True)
    json_file = f"reports/compliance_report_{ts}.json"
    md_file = f"reports/compliance_report_{ts}.md"

    with open(json_file, "w") as f:
        json.dump(report, f, indent=2)
    print(f"✅ JSON report: {json_file}")

    with open(md_file, "w") as f:
        f.write(build_markdown(report))
    print(f"✅ Markdown report: {md_file}")

    s = report["summary"]
    total_v = s["group_folders_violations"] + s["calendars_violations"] + s["collectives_violations"] + s["talk_rooms_violations"]
    total_w = s["group_folders_warnings"]   + s["calendars_warnings"]   + s["collectives_warnings"]   + s["talk_rooms_warnings"]
    print(f"\n📊 Summary: {total_v} violation(s), {total_w} warning(s), {s['multi_wg_users']} multi-WG user(s)")


if __name__ == "__main__":
    main()
