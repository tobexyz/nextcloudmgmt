
# Working Group Permission Structure

detailed summary of the permission structure automatically created by the script for each new Working Group (WG). The system follows the principle: **"Internal matters remain protected, knowledge is shared."**

### 1. File Directories (Group Folders)

The folder structure is designed to allow the WG to work autonomously while maintaining transparency for the rest of the organization.

* **Main Directory (WG-Name):**
  * **Admin:** The only assigned group.
  * **WG-Members:** No access.
  * **Rest of the organization (users):** No access.
* **Subfolder `01_<WG-Name>_Public`:**
  * **Read access for everyone.** Ideal for final minutes or publications.
* **Subfolders `02_<WG-Name>_Private` / `02_<WG-Name>_Archive`:**
  * **WG-Members:** Full access.
  * **Rest of the organization:** Hidden. These folders do not even appear in the web interface of other users (ACL mask 0).

### 2. Knowledge & Documentation (Collectives / Wiki)

The Wiki serves as the central memory of the working group.

* **Access Type:** Public Circle (Type 2).
* **WG-Members:** Have **Contributor** status within the Circle; they can create, edit, and delete pages.
* **Rest of the organization:** Assigned the **Member** role. They can access the Collective via the app and read all content but cannot make changes. This promotes knowledge exchange within the NGO. *(Note: This is the theoretical goal; implementation is still in progress.)*

### 3. Schedules & Planning (Calendar)

Each WG receives its own calendar to provide transparency regarding meetings and deadlines.

* **Owner:** The `anchor_user` (technical administrator).
* **WG-Members:** Receive a share with **read-write** permissions. They can create and reschedule appointments.
* **Rest of the organization:** Receives an automatic **read-only** share. This allows everyone to see when a WG meets without interfering with the schedule.

### 4. Talk channel

Each WG has a channel in Talk. The room is open for every registered user but initially only the WG is member of the channel

### Summary of Technical Permission Logic (Bitmasks)

The script utilizes Nextcloud bitmasks to strictly enforce these rules:

| Component | Target Audience | Permission (Technical) | Effect |
| :--- | :--- | :--- | :--- |
| **Group Folder** | Admin | 31 | Full Access |
| **Group Folder** | Everyone (users) | 0 | No Access |
| **Internal Folder** | Everyone (users) | 0 | Invisible |
| **Internal Folder** | WG-Members | 31 | Full Access |
| **Public Subfolder** | Everyone (users) | 1 | Read Only |
| **Public Subfolder** | WG-Members | 31 | Full Access |
| **Calendar** | WG-Group | read-write | Editing enabled |
| **Calendar** | Everyone (users) | read | View appointments only |
| **Collective** | Circle-Member (WG-Group) | Moderator | Edit Wiki |
| **Collective** | Circle-Member (Group All) | Member | Read  |
| **Talk WG-Group channel** | WG-Member | isMember | joinable NGO wide |
| **Talk WG-Group channel** | Everyone (users) | can join if wanted | joinable NGO wide |
