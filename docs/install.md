# Installation

Arpenteur runs on your own machine. It needs three things from the outside, and this page installs them in order:

1. Docker, which runs the PostgreSQL database in a container
2. uv, which installs Python and the application
3. Either the `claude` command signed in to a Claude subscription, or an API key

Allow half an hour the first time. Nothing here needs an account other than the ones named.

## The operating system

Arpenteur is developed under Linux and run daily under Windows through WSL2. The steps differ only in how Docker and a terminal are obtained.

**Linux**

Install Docker Engine with the compose plugin from your distribution, and add your user to the `docker` group so the commands below run without `sudo`. Any shell works.

**macOS**

Install Docker Desktop from docker.com and start it once. The terminal is the one the system ships. Homebrew is the easiest way to install uv and Node, and the commands below use it.

**Windows**

Install WSL2 with an Ubuntu distribution. In a PowerShell window run as administrator:

```powershell
wsl --install
```

Then install Docker Desktop for Windows, and under its settings turn on the WSL2 backend and the integration with the Ubuntu distribution. Every command on this page is then typed in the Ubuntu terminal, not in PowerShell. Docker Desktop must be running before the database can start; when `docker ps` answers that the daemon is not reachable, start Docker Desktop from the Windows start menu, or from the Ubuntu terminal:

```bash
nohup "/mnt/c/Program Files/Docker/Docker/Docker Desktop.exe" >/dev/null 2>&1 &
```

Wait until `docker ps` answers with a table.

## uv

uv installs the Python interpreter the project asks for, and every package, into a folder under the project. Nothing else on the machine is touched.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On macOS with Homebrew, `brew install uv` does the same. Open a new terminal afterwards so the command is found.

## The application

Clone the repository into a folder of your own, then let uv set it up:

```bash
git clone <the repository address> arpenteur
cd arpenteur
uv sync
```

`uv sync` fetches a Python of the right version when the machine has none, creates the virtual environment and installs the dependencies. It takes a minute on an ordinary connection.

## The database

The database runs in a container, bound to the machine itself: it listens on `127.0.0.1:5433` and nowhere else.

```bash
docker compose up -d
uv run arpenteur init-db
```

The first command downloads the PostgreSQL image, about 400 MB, and starts it. The second creates the schema of every module and prints their versions. Run it again after every update of the code: it applies whatever is new and changes nothing else.

The data lives in a Docker volume called `arpenteur-pgdata`. It survives stopping the container and restarting the machine. [Operations](operations.md) says how to back it up.

## The environment file

Credentials never go in the configuration. They live in `.env`, a file the repository ignores:

```bash
cp .env.example .env
```

Open `.env` in any editor. Every name in it is explained in the file and in [the setup page](setup.md). For a first run, leave everything empty except the register credentials once they arrive.

## The register credentials

The companies come from Zefix, the federal index of the commercial register. Access is free and personal: write to zefix@bj.admin.ch asking for API credentials, with your name and what you survey. They arrive by email, usually within a few working days. Put them in `.env` as `ZEFIX_USER` and `ZEFIX_PASSWORD`.

Until they arrive, the sweep, the detail fetch and the register sync refuse and say so. Everything else opens, and the Settings page can be filled in.

## Model access

Several stages call a model: sorting companies into segments, the website research, the scoring, the drafts, the suggested approach and the meeting preparation. There are two ways to pay for those calls.

**A Claude subscription**

The subscription is used through the `claude` command, signed in once on this machine. It needs Node 18 or later.

```bash
npm install -g @anthropic-ai/claude-code
claude
```

The first start opens a browser to sign in with your plan. The sign-in is kept by the command itself; nothing is copied into Arpenteur. This is the default: Model access under Settings is set to the subscription.

**An API key**

Create a key at console.anthropic.com, put it in `.env` as `ANTHROPIC_API_KEY`, and under Settings, Model access, choose the key. Calls are then billed per use, and no command needs installing. A third setting, the subscription when signed in and otherwise the key, suits a machine that is sometimes signed in.

## The first check

```bash
uv run arpenteur doctor --quick
```

The doctor names everything a first run needs and says which pieces are missing, each with the place to set it. On a fresh installation it names the register credentials, the scoring brief and the mailbox. The same list is on the Home page of the console.

## The console

```bash
uv run arpenteur console
```

Open `http://127.0.0.1:8000` in a browser. The console has no sign-in because it listens on the machine itself only; [the setup page](setup.md) says what to fill in first. Stop it with Ctrl-C. A stage running inside the console at that moment stops with it, and is marked as interrupted at the next start.

## The console in a container

A machine with Docker and nothing else can run the console from the compose file too, under the `app` profile:

```bash
docker compose --profile app up -d --build
```

The project folder is mounted into the container, so the configuration, the brief, the templates and the data stay on the host. Model access inside the container is by API key, because the `claude` command and its sign-in are per machine. The console answers at the same address.

## Updating

```bash
git pull
uv sync
uv run arpenteur init-db
```

The third command applies the new migrations. [Operations](operations.md) explains the one refusal it can give, when a migration file changed after being applied.
