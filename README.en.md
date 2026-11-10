[Español](README.md) | **English**

# Alfresco Community Docker Manager

Install and manage **Alfresco Community** on your Linux server from a simple
menu, without writing configuration files. It uses Alfresco's official Docker
images and speaks English and Spanish.

Version **0.1.0** · [MIT License](LICENSE)

![Alfresco Community Docker Manager main menu in English](docs/images/main-menu-en.png)

*Main menu with a running installation.*

## What you can do

- **Install Alfresco in minutes:** the manager downloads the newest official
  release and sets everything up: Share, Content App, Control Center,
  Elasticsearch search and a PostgreSQL database.
- **Stay safe from the start:** the factory `admin/admin` password is replaced
  with a strong one before any access is opened, and each installation generates
  its own encryption keys.
- **Turn on HTTPS:** with your own domain (free Let's Encrypt certificate),
  through Cloudflare Tunnel, or on your local network with a private CA.
- **Update in one step:** the manager tells you when Alfresco publishes a new
  release and applies it after saving the database. If something fails, it goes
  back to the previous version.
- **Use the server's memory:** Alfresco gets more memory on servers with 16 GB
  or more, with nothing to configure.
- **Send email (optional):** invitations, notifications and tasks through your
  mail server.
- **Check health:** services, addresses, logs and health checks whenever you
  need them.
- **Change passwords** for any user and **free up space** from old versions.

## Before you start

| | Minimum | Recommended |
| --- | --- | --- |
| Memory (RAM) | **8 GB** | 16 GB |
| Disk | **15 GB free** | A **50 GB** disk (or more, depending on your documents) |
| Processors | 2 | 4 |
| System | 64-bit Linux (amd64 or arm64) with **sudo** access | |

You also need Internet access to download Alfresco. If Docker or Python is
missing, the wizard offers to install it.

Before installing, the manager checks your server and shows the result:

![Requirements review before installing](docs/images/requirements-en.png)

- **Memory:** with less than 8 GB it warns that Alfresco will probably fail to
  start for lack of memory. You may continue at your own risk.
- **Disk:** the installation takes about 12 GB. With less than 15 GB free, or
  if the disk would end up above 85 % (where search stops indexing), it does not
  install. With less than 35 GB free it warns that updates will need
  «Free up space» first.

For example, a 30 GB disk with a fresh Ubuntu leaves about 23 GB free:
Alfresco fits, but each update will need some space freed first. With a 50 GB
disk you update without worrying and keep about 13–20 GB for documents.

## Get started

**1. Download the manager and open it**

```bash
git clone https://github.com/solqubit/alfresco-community-docker-manager.git
cd alfresco-community-docker-manager
sudo bash start.sh
```

**2. Follow the wizard**

1. Choose the language.
2. Choose **Install Alfresco** and the kind of server:
   - **Local server:** opened from your network, for example `http://192.168.1.10`.
   - **VPS:** at first only reachable through an SSH tunnel (safer), until you
     add your domain.
3. Review the proposed values (IP, ports, timezone, data folder and, if you
   want, outbound email) and confirm. The first installation takes a few minutes.

**3. Sign in to Alfresco**

When it finishes, the manager shows the addresses and the **admin password**.
Keep it safe; you can show it again in **Accounts → Access credentials**.

| Application | Address |
| --- | --- |
| Alfresco Share (main interface) | `http://YOUR_IP/share/` |
| Alfresco Content App | `http://YOUR_IP/content-app/` |
| Control Center (administration) | `http://YOUR_IP/control-center/` |

On a VPS, open the tunnel from your computer and browse to
`http://127.0.0.1:8080/share/`:

```bash
ssh -L 8080:127.0.0.1:80 user@YOUR_VPS_IP
```

**4. Add HTTPS (recommended)**

In **Settings → Domains and access**:

- **Your domain on a VPS:** point DNS to your server, open ports 80 and 443
  and add the domain. The certificate is requested and renewed automatically.
- **Cloudflare Tunnel:** add the domain in tunnel mode.
- **Local network:** see the next section.

### HTTPS on your local network (IP or local name)

You can reach the server by its **IP** or by a **local name** such as
`alfresco.lan`. The IP is enough; a name is optional and just easier to
remember.

1. In **Domains and access → Local addresses → Add another local address**, type
   the IP or the name and set **Use local HTTPS: Yes**. The manager creates the
   certificate automatically; there is nothing to generate.
2. If you use a **name**, point it to your server: in the router or network DNS
   (for example `alfresco.lan → 192.168.1.10`) or in each computer's `hosts`
   file. Avoid names ending in `.local`; prefer `.lan` or `.internal`.
3. Export the CA in **Export CA for local HTTPS** and install it once on each
   computer so browsers do not warn.

Each certificate only covers its exact address: if you use both the IP and the
name, enable HTTPS on both. The CA is the same for all. The list shows each
address with its type and state, for example
`https://alfresco.lan · Local name · Local HTTPS on`.

## Outbound email (optional)

Without email Alfresco works the same but sends no site invitations,
notifications or task emails. Configure it while installing or later in
**Settings → Outbound email**. You need an SMTP sending account from your mail
provider or a sending service:

| Setting | Example |
| --- | --- |
| SMTP server | `smtp.office365.com`, `smtp.gmail.com`, `mail.yourcompany.com` |
| Port and security | `587` with STARTTLS (usual), `465` with SSL |
| Username and password | The sending account (Gmail needs an app password) |
| Sender | `alfresco@yourcompany.com` |

Before saving, the manager sends a **test message** to confirm the settings
work. The password is stored privately and never appears in logs. Alfresco
restarts to apply the change (1-3 minutes).

## Memory

The manager sizes each service from the server RAM:

| Server RAM | Alfresco | Elasticsearch | Transformations | Share |
| --- | --- | --- | --- | --- |
| 8 GB | 2 GB | 2 GB | 1.5 GB | 1 GB |
| 16 GB | 4 GB | 3 GB | 2 GB | 1.5 GB |
| 32 GB or more | 8 GB | 4 GB | 3 GB | 2 GB |

If you add RAM to your server, it is applied the next time you start Alfresco,
update or change settings from the manager.

## Open the manager again

From any folder:

```bash
sudo bash /opt/alfresco-community-docker-manager/start.sh
```

Move with **↑ / ↓**, switch columns with **← / →**, open with **Enter** and go
back with **Esc**.

## Updates

**Updates → Check and apply updates** shows whether a new Alfresco release or
component update is available. When you apply it, the manager:

1. Stops Alfresco briefly and saves the database.
2. Updates every component that changes, together.
3. Starts everything and checks that it responds.

Before starting, the manager estimates the space the new images need (from
about 1 GB when only Share changes to about 13 GB when almost everything does).
If there is not enough, it does not start and asks you to use
**Health → Free up space**. Previous versions use disk until you do.

Your documents are never touched. If an update is interrupted or fails, open
**Recover pending operation** to resume it or go back to the previous version.

To update the manager itself, download the new version and choose
**Manager → Update the manager**.

## Where your data lives

Everything stays in **`/opt/alfresco-community-docker-manager/`**, outside the
downloaded folder: configuration, documents, database, certificates and logs.
You can delete the downloaded folder after installing.

> The manager does not make full backups. To protect yourself against losing the
> server, use your VPS snapshots or an external copy of the disk.

## Help

- **Something does not start:** open **Service → Logs** and pick the service.
  If services stop on their own, memory is usually short (8 GB minimum).
- **Disk almost full:** **Health → Health checks** warns from 80 %. Use
  **Health → Free up space** to remove old versions.
- **Emails do not arrive:** **Settings → Outbound email → Send test message**
  shows why (username or password, blocked port…). Many VPS providers block
  port 25: use 587.
- **Search does not find something:** in Share, set the scope to
  **Repository** and use `*` to match part of a word (`infor*`). New documents
  take a few seconds to appear.
- **Commands without the menu:** `sudo bash start.sh --help` lists every action
  (status, update, domains, logs…).

[Validation report (Spanish)](docs/VALIDACION.es.md) · Independent project based
on GLPI Docker Manager and Nextcloud Docker Manager. Alfresco, PostgreSQL,
Elasticsearch and Caddy keep their own licenses.
