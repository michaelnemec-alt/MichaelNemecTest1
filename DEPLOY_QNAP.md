# Self-hosting on QNAP (TS-464) with Container Station

Run the AutoStore analytics app on your QNAP instead of Streamlit Community Cloud.
The TS-464 (Intel Celeron N5095, x86-64, 4 GB RAM) runs Docker natively via
Container Station and gives the app far more memory than the ~1 GB free tier.

## 1. Prerequisites

- **Container Station** installed (QNAP App Center).
- SSH enabled on the NAS (Control Panel → Network & File Services → Telnet/SSH),
  or use Container Station's GUI (see step 4b).
- Your **CubeAnalytics API token**.

## 2. Get the code onto the NAS

SSH into the NAS and clone the repo into a shared folder, e.g. `/share/Container`:

```sh
cd /share/Container
git clone https://github.com/michaelnemec-alt/MichaelNemecTest1.git
cd MichaelNemecTest1
```

## 3. Configure the secret

```sh
cp .env.example .env
# edit .env and set CUBEANALYTICS_TOKEN=...  (nano .env)
```

`.env` is git-ignored, so the token stays only on your NAS — never in git and
never baked into the image.

## 4a. Start it (SSH / docker compose)

```sh
docker compose up -d --build
```

- First build takes a few minutes (installs Python deps).
- `restart: unless-stopped` → it comes back automatically after a reboot/crash.
- Update later with:
  ```sh
  git pull && docker compose up -d --build
  ```

## 4b. Start it (Container Station GUI)

1. Container Station → **Create** → **Application** (Docker Compose).
2. Paste the contents of `docker-compose.yml`.
3. Add the environment variable `CUBEANALYTICS_TOKEN` in the app settings.
4. Create → it builds and starts.

## 5. Open the app

- **On your home network:** `http://<NAS-IP>:8501`
  (find the NAS IP in Qfinder or Control Panel → Network).

## 6. Access from outside the home (recommended: keep it private)

The app pulls internal Rohlik operational data — do **not** expose port 8501
directly to the public internet. Instead:

- **Tailscale** (QNAP App Center): install, log in, then reach the app at
  `http://<nas-tailscale-name>:8501` from any of your devices. Simplest + secure.
- or **QNAP QVPN** (WireGuard/OpenVPN) and connect to the home network first.

## Health & troubleshooting

- Container has a healthcheck hitting `/_stcore/health`; Container Station shows
  it as *healthy* once up.
- Logs: `docker compose logs -f` (or the GUI's log view).
- Memory is capped at 3 GB (`mem_limit` in compose) so the app can't starve the
  NAS; raise/lower it to taste. Consider adding a RAM stick (TS-464 goes to
  16 GB) if you run many other containers.
