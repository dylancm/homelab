# VM Deployment Guide

Deploy KVM virtual machines on ZFS with bridged networking and optional public access via Cloudflare Tunnels.

## Architecture

```
Internet → HTTPS → Cloudflare Edge → Tunnel → LAN → VM
```

- **Storage:** ZFS zvols for VM block devices (snapshots, compression, rollback)
- **Networking:** VMs bridged directly to the LAN — they get real IPs from the router
- **DNS:** OPNsense Unbound registers DHCP leases dynamically — VMs are reachable at `<name>.home.nthparallel.com` automatically
- **Public access:** Cloudflare Tunnel routes (no open ports, no SSL cert management)

---

## Prerequisites

- KVM/libvirt installed on the host
- A ZFS pool (e.g. `tank`) with a `tank/vms` dataset
- A bridge interface on the host (e.g. `br-enp2s0`)
- For public access: a Cloudflare Tunnel connector running on the LAN

### Install KVM/libvirt

```bash
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients virtinst bridge-utils cloud-image-utils
sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt $USER
```

### Define a libvirt network for the host bridge

This only needs to be done once. It tells libvirt about the existing bridge so VMs can attach to it.

```bash
cat <<EOF | sudo virsh net-define /dev/stdin
<network>
  <name>host-bridge</name>
  <forward mode="bridge"/>
  <bridge name="br-enp2s0"/>
</network>
EOF

sudo virsh net-start host-bridge
sudo virsh net-autostart host-bridge
```

Replace `br-enp2s0` with your bridge interface name.

---

## Creating a VM

VMs are provisioned from a golden base image using `vm-create`. The entire process is automated — no interactive installer, no manual post-install steps.

### Normal workflow

```bash
vm-create --name <vm-name>
```

The script:
1. Clones `tank/vms/ubuntu-base@ready` to a new zvol
2. Generates a per-VM cloud-init seed ISO (sets hostname, injects SSH key, configures UFW, installs packages)
3. Boots the VM via `virt-install --import` (no ISO needed)
4. Waits for SSH to become available at `<vm-name>.home.nthparallel.com`
5. Waits for cloud-init to finish (`cloud-init status --wait`)
6. Reboots the VM so the QEMU guest agent starts cleanly
7. Takes a `@fresh` ZFS snapshot
8. Enables autostart on host boot
9. Prints the IP, SSH command, and remaining manual steps

**Options:**

```bash
vm-create --name <name>                          # defaults: 4 vCPUs, 8GB RAM, 20G disk
vm-create --name <name> --ram 16384 --cpus 8    # custom resources
vm-create --name <name> --disk 100G             # custom disk size
```

**After the script completes:**
- Assign a static DHCP lease in OPNsense (MAC address is printed by the script)
- Add a Caddy reverse proxy entry + Cloudflare tunnel route if public access is needed

### What's in the base image (cloud-init)

Every VM cloned from the base gets:
- `dylan` user with sudo (no password) and SSH key injected
- Password SSH auth disabled
- UFW enabled — deny all inbound except SSH
- `qemu-guest-agent`, `curl`, `vim`, `unattended-upgrades` installed
- Automatic security updates configured
- Fresh machine-id generated (ensures unique DHCP lease from first boot)

---

## Setting up the base image (one-time)

If the base image needs to be recreated from scratch:

### 1. Download the Ubuntu 24.04 cloud image

```bash
wget -P /tmp https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
```

### 2. Create the base zvol and write the image

```bash
sudo zfs create -V 20G -o compression=lz4 -o volblocksize=64k tank/vms/ubuntu-base
sudo qemu-img convert -f qcow2 -O raw /tmp/noble-server-cloudimg-amd64.img /dev/zvol/tank/vms/ubuntu-base
```

### 3. Take the base snapshot

```bash
sudo zfs snapshot tank/vms/ubuntu-base@ready
```

### 4. Create the cloud-init config

```bash
mkdir -p ~/vm-init && cat > ~/vm-init/user-data <<'EOF'
#cloud-config

users:
  - name: dylan
    groups: sudo
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJST7uGpGw3RStcmqJHA4WvyVKx++WtcJJdOpPMAekQI dylan@sim-server

ssh_pwauth: false

packages:
  - qemu-guest-agent
  - curl
  - vim
  - unattended-upgrades

package_update: true
package_upgrade: true

write_files:
  - path: /etc/apt/apt.conf.d/20auto-upgrades
    content: |
      APT::Periodic::Update-Package-Lists "1";
      APT::Periodic::Unattended-Upgrade "1";
  - path: /etc/systemd/system/qemu-guest-agent.service.d/override.conf
    content: |
      [Unit]
      After=cloud-init.target
      [Service]
      Restart=on-failure
      RestartSec=5s

runcmd:
  - rm -f /etc/machine-id
  - systemd-machine-id-setup
  - ufw default deny incoming
  - ufw default allow outgoing
  - ufw allow ssh
  - ufw --force enable
  - systemctl daemon-reload

final_message: "Cloud-init complete. VM ready after $UPTIME seconds."
EOF

cat > ~/vm-init/meta-data <<'EOF'
instance-id: ubuntu-base
local-hostname: ubuntu-base
EOF
```

### 5. Create the seed ISO directory and build the ISO

```bash
sudo mkdir -p /var/lib/libvirt/cloud-init
cloud-localds ~/vm-init/seed.iso ~/vm-init/user-data ~/vm-init/meta-data
sudo cp ~/vm-init/seed.iso /var/lib/libvirt/cloud-init/seed.iso
```

### 6. Install vm-create

```bash
sudo install -m 755 vm-create /usr/local/bin/vm-create
```

The `vm-create` script and the seed ISO template live at:
- `/usr/local/bin/vm-create` — the provisioning script
- `~/vm-init/user-data` — cloud-init template (edit to change base config)
- `~/vm-init/seed.iso` + `/var/lib/libvirt/cloud-init/seed.iso` — built seed ISO

---

## Common VM Management

### Find the VM's IP

```bash
# Via guest agent (works after first reboot post-cloud-init)
sudo virsh domifaddr <vm-name> --source agent

# Via DNS (works as soon as DHCP lease is assigned)
dig +short <vm-name>.home.nthparallel.com
```

### Start / stop / restart

```bash
sudo virsh start <vm-name>
sudo virsh shutdown <vm-name>      # graceful
sudo virsh destroy <vm-name>       # force off
sudo virsh reboot <vm-name>
```

### Delete a VM

```bash
sudo virsh destroy <vm-name>
sudo virsh undefine <vm-name> --nvram
sudo zfs destroy -r tank/vms/<vm-name>
sudo rm -f /var/lib/libvirt/cloud-init/<vm-name>-seed.iso
```

### ZFS snapshots

```bash
# Snapshot before risky changes
sudo zfs snapshot tank/vms/<vm-name>@<label>

# Roll back (VM must be stopped)
sudo virsh destroy <vm-name>
sudo zfs rollback tank/vms/<vm-name>@<label>
sudo virsh start <vm-name>

# List snapshots
sudo zfs list -t snapshot -r tank/vms

# Delete a snapshot
sudo zfs destroy tank/vms/<vm-name>@<label>
```

### Autostart on host boot

```bash
sudo virsh autostart <vm-name>
```

---

## VM Base Configuration

VMs provisioned via `vm-create` are already configured. For any additional setup (Docker, etc.):

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect
```

No other inbound ports need to be opened if using Cloudflare Tunnels — all public traffic arrives through the tunnel via the LAN.

---

## Exposing Services via Cloudflare Tunnel

If there is already a cloudflared connector running on the LAN, just add public hostname routes pointing to the VM's static IP.

**Important:** The cloudflared Docker container must run with `--network host` to reach other machines on the LAN.

### Add public hostname routes

In the Cloudflare One dashboard at [one.dash.cloudflare.com](https://one.dash.cloudflare.com/):

1. Go to **Networks → Connectors**
2. Click the **three dots** next to your tunnel → **Configure**
3. Go to the **Published application routes** tab
4. Click **Add a published application route**

For each service:

| Field | Value |
|-------|-------|
| Subdomain | e.g. `myapp` |
| Domain | Select your domain |
| Service type | `HTTP` (not HTTPS — TLS terminates at Cloudflare) |
| URL | `<vm-static-ip>:<port>` |

Cloudflare automatically creates the CNAME DNS records. HTTPS is handled at Cloudflare's edge.

### Protect admin interfaces with Cloudflare Access

Any admin UI or dashboard should have a Cloudflare Access policy so it isn't open to the internet.

1. In the Cloudflare One dashboard, go to **Access → Applications**
2. Click **Add an application → Self-hosted**
3. Set the application domain to match the tunnel hostname
4. Add a policy: Action = Allow, Selector = Emails, Value = your email
5. Save

Users must verify a one-time PIN sent to their email before they can reach the service.

### Cloudflare prerequisites

- Domain must be managed by Cloudflare (nameservers pointed to Cloudflare)
- **Always Use HTTPS** should be enabled under SSL/TLS → Edge Certificates in the main Cloudflare dashboard
- Single-level subdomains (e.g. `app.example.com`) are covered by the free Universal SSL certificate — multi-level subdomains (e.g. `api.app.example.com`) require an Advanced Certificate

---

## Troubleshooting

### vm-create says ZFS dataset already exists

A previous failed run left the dataset behind. Clean it up:

```bash
sudo virsh destroy <vm-name> 2>/dev/null || true
sudo virsh undefine <vm-name> --nvram 2>/dev/null || true
sudo zfs destroy -r tank/vms/<vm-name>
sudo rm -f /var/lib/libvirt/cloud-init/<vm-name>-seed.iso
```

### VM provisioned but guest agent not responding

The QEMU guest agent only starts cleanly after the first reboot post-cloud-init. If `vm-create` completes but the agent isn't connected:

```bash
sudo virsh reboot <vm-name>
sleep 20
sudo virsh domifaddr <vm-name> --source agent
```

### cloud-init stuck / hung

If cloud-init hangs during provisioning, check what's running inside the VM:

```bash
ssh dylan@<vm-name>.home.nthparallel.com "ps aux | grep -E 'apt|dpkg|systemctl|cloud'"
```

A hanging `systemctl` call is the most common cause. Kill the stuck process and cloud-init will mark the run as errored but the VM will still be usable.

### virt-install says the disk is already in use

The old VM definition is still registered. Undefine it first:

```bash
sudo virsh undefine <vm-name> --nvram
```

### VM boots to BIOS / can't find boot device

The zvol is blank — the base image was never written. Re-run the base image setup from scratch.

### Cloudflare Tunnel returns bad gateway

1. **Check the service type** in the tunnel config — it should be `HTTP`, not `HTTPS`.
2. **Verify the port** matches what's actually running in the VM. Check with `curl -I http://<vm-ip>:<port>` from the machine running cloudflared.
3. **Check cloudflared networking** — if cloudflared runs in Docker, it needs `--network host` to reach the LAN:
   ```bash
   docker inspect cloudflared | grep NetworkMode
   ```
4. **Check cloudflared logs:**
   ```bash
   docker logs cloudflared --tail 50
   ```

### Docker container port not reachable from LAN

If `docker ps` shows a port as `3000/tcp` (no `0.0.0.0:` prefix), it's only exposed inside Docker's network, not mapped to the host. Add a port mapping in `docker-compose.yml`:

```yaml
services:
  myservice:
    ports:
      - "3000:3000"
```

Then restart: `docker compose up -d myservice`
