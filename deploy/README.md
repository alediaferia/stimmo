# Stimmo deployment runbook

Production host: Hetzner CX22, Falkenstein. All inbound traffic routed via Cloudflare Tunnel — no public ports except SSH (22/tcp).

## Rollback

Each deploy tags the image `:sha-<7-char>`. To roll back:

1. Find the last good SHA from the GitHub Actions run history (or `docker image ls` on the box).
2. SSH in and rewrite the tag:
   ```sh
   ssh deploy@<SSH_HOST>
   IMAGE="ghcr.io/alediaferia/stimmo:sha-<good-sha>"
   sed -i "s|image: ghcr.io/alediaferia/stimmo:.*|image: $IMAGE|" /opt/stimmo/docker-compose.yml
   docker compose -f /opt/stimmo/docker-compose.yml pull
   docker compose -f /opt/stimmo/docker-compose.yml up -d --remove-orphans
   ```
3. Alternatively, re-run the successful workflow from the GitHub Actions UI — it will redeploy that SHA.

## Key rotation (SSH deploy key)

1. Generate a new ed25519 key pair locally: `ssh-keygen -t ed25519 -f stimmo_deploy`
2. Add the new public key to the `deploy` user on the box:
   ```sh
   ssh deploy@<SSH_HOST> "echo '<new-pub-key>' >> ~/.ssh/authorized_keys"
   ```
3. Update the `SSH_KEY` GitHub repo secret with the new private key.
4. Verify the new key works: `ssh -i stimmo_deploy deploy@<SSH_HOST> whoami`
5. Remove the old public key from `~/.ssh/authorized_keys` on the box.

## GHCR PAT rotation

1. Generate a new classic PAT scoped to `read:packages` only at github.com/settings/tokens.
2. Update `/opt/stimmo/.env` on the box:
   ```sh
   ssh deploy@<SSH_HOST>
   # edit GHCR_PAT= line
   nano /opt/stimmo/.env
   ```
3. Re-login Docker with the new PAT:
   ```sh
   source /opt/stimmo/.env
   docker login ghcr.io -u alediaferia -p "$GHCR_PAT"
   ```
4. Revoke the old PAT at github.com/settings/tokens.

## Cloudflare Tunnel re-creation

If the tunnel token is lost or compromised:

1. In the Cloudflare Zero Trust dashboard, delete the `stimmo-prod` tunnel.
2. Create a new tunnel named `stimmo-prod`, configure the public hostname `stimmo.it → http://stimmo:8000`.
3. Copy the new tunnel token. Update `/opt/stimmo/.env` on the box:
   ```sh
   ssh deploy@<SSH_HOST>
   nano /opt/stimmo/.env   # update TUNNEL_TOKEN=
   ```
4. Restart the cloudflared container:
   ```sh
   docker compose -f /opt/stimmo/docker-compose.yml restart cloudflared
   ```

## Rebuild from scratch

The host is fully rebuildable from committed config in under 10 minutes:

1. Create a new VPC instance using `deploy/cloud-init.yml`
   - Replace `REPLACE_WITH_DEPLOY_PUBLIC_KEY` with the deploy user's public SSH key before pasting.
2. After provisioning completes, SSH in as `deploy` and populate `/opt/stimmo/.env`:
   ```
   TUNNEL_TOKEN=<from Cloudflare dashboard>
   GHCR_PAT=<read:packages PAT>
   ```
3. Start the stack:
   ```sh
   systemctl start stimmo.service
   ```
4. Verify: `docker compose -f /opt/stimmo/docker-compose.yml ps` — both services should be running/healthy.
5. Hit `https://stimmo.it` from a browser to confirm end-to-end.
6. Update the `SSH_HOST` GitHub repo secret if the IP changed.
7. Decommission the old server.

## Useful commands on the box

```sh
# Check service status
docker compose -f /opt/stimmo/docker-compose.yml ps

# Tail logs
docker compose -f /opt/stimmo/docker-compose.yml logs -f

# Check which image tag is running
docker inspect $(docker compose -f /opt/stimmo/docker-compose.yml ps -q stimmo) \
  | jq -r '.[].Config.Image'

# Verify UFW rules
ufw status verbose

# Check unattended-upgrades dry run
unattended-upgrades --dry-run --debug
```
