# HarmonAIze

An open-source toolkit that streamlines the process of harmonising and integrating climate and health data. Designed for research teams in resource-constrained settings, it leverages AI-assisted workflows to map, clean, and unify heterogeneous datasets, all while preserving privacy through federated approaches. By aligning health data with curated climate data sources, HarmonAIze accelerates climate–health research and enables faster, more accurate analyses of environmental impacts on health outcomes.

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

License: MIT

**Demos**

- **Health**: https://witsphr.box.com/s/snf03o3aq6psvmcq5294jhwmgdb7bcfr
- **Geocoding**: In production
- **Climate**: In production

**The Django application lives in the `harmonaize/` directory. Unless stated otherwise, run every command in this README from inside `harmonaize/`.**

For contributor expectations, including the preference for small, transparent pull requests, see [CONTRIBUTORS.md](CONTRIBUTORS.md). For how issues are labelled, prioritised and picked up, see [Contributing and issue workflow](#contributing-and-issue-workflow). To report a security vulnerability, follow [SECURITY.md](SECURITY.md) and do not open a public issue.

## Quick Start

**TL;DR**: This project runs entirely in Docker containers. Here's how to get started:

1. **Install Docker Desktop** (https://www.docker.com/products/docker-desktop/) and **[just](https://github.com/casey/just)** (`brew install just`, or see its install guide)
2. **Clone and navigate**: Clone this repository and navigate to the `harmonaize` directory
   ```bash
   git clone https://github.com/drnicholasbrink/HarmonAIze.git HarmonAIzeToolkit
   cd HarmonAIzeToolkit/harmonaize
   ```
3. **First start: build and start with `just`**:
   ```bash
   just build
   just up
   ```
   On first start this also creates `./.envs/.local/.django` and fills every placeholder secret with a random value, so there is nothing to edit by hand. It lists the optional keys (OpenAI, Mapbox, Google Earth Engine) you can add afterwards.
4. **Make and run database migrations** (only the `0001` migrations are committed, so generate the rest locally; the Django container also runs `migrate` on start):
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations
   ```
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate
   ```
5. **Create a superuser**:
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser
   ```
6. **Visit the application**: http://localhost:8000

**Next time** you only need the normal start command; your env files and secrets are kept as they are:
```bash
just up    # or: docker compose -f docker-compose.local.yml up -d
```

**Important**: No need to install Python packages locally - everything runs in Docker containers!

## Analysis module (DataSHIELD via Armadillo) - local setup

The analysis module now supports local DataSHIELD-ready export workflows backed by Armadillo.

This repository expects Armadillo as a pinned git submodule at `harmonaize/vendor/molgenis-service-armadillo`.
The current pinned commit is `6864b346df28f4c05065dfab35149dad29a1990d` from the `harmonaize-integration` branch of `drnicholasbrink/molgenis-service-armadillo`.

The local analysis stack does not use the Armadillo image alone. It read-only bind-mounts Armadillo quickstart configuration and Keycloak realm import files from the pinned checkout. Mutable Armadillo data and logs use Docker-managed named volumes so that Docker Desktop sleep/wake cycles do not leave the services dependent on a stale macOS file-sharing mount.

1. Clone this repository with submodules, or initialise the submodule after cloning:
   ```bash
   git clone --recurse-submodules <repository-url>
   cd HarmonAIzeToolkit

   # If you already cloned without submodules
   git submodule update --init --recursive
   ```
2. Start the stack (including Armadillo and a dedicated snapshot PostgreSQL instance):
   ```bash
   COMPOSE_PROFILES=analysis docker compose -f docker-compose.local.yml up -d
   ```
3. Create and apply migrations:
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations analysis
   docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate
   ```
4. Open services:
   - HarmonAIze: http://localhost:8000
   - Armadillo: http://localhost:8001

If Armadillo integration is not needed, leave `ANALYSIS_ENABLED=false` and do not start the `analysis` profile. In that mode, no Armadillo submodule checkout is required.

### Recovering after Docker Desktop sleep or a resource deadlock

`AnalysisSyncLock` is a database-backed single-flight application lock with a 15-minute TTL. Expired rows are reclaimable automatically and should not be deleted during every startup. An operating-system error such as `Resource deadlock avoided`/`EDEADLK` instead points to Docker Desktop's host file-sharing layer, especially when both Django's reload watcher and Armadillo storage fail together.

Use this non-destructive restart order (it does not remove volumes):

```bash
docker compose -f docker-compose.local.yml stop django celeryworker celerybeat flower armadillo rserver keycloak
COMPOSE_PROFILES=analysis docker compose -f docker-compose.local.yml up -d keycloak rserver
COMPOSE_PROFILES=analysis docker compose -f docker-compose.local.yml up -d armadillo
docker compose -f docker-compose.local.yml up -d django celeryworker celerybeat flower
```

If Docker commands themselves return `Resource deadlock avoided`, restart Docker Desktop before running the sequence; an application startup hook cannot safely clear a stale host filesystem mount.

Older checkouts stored Armadillo state under `vendor/molgenis-service-armadillo/docker/quickstart/data`. The new named volume starts empty. To retain that state, copy it once while Armadillo is stopped:

```bash
docker compose -f docker-compose.local.yml stop armadillo
docker run --rm \
  -v harmonaize_harmonaize_local_armadillo_data:/target \
  -v "$PWD/vendor/molgenis-service-armadillo/docker/quickstart/data:/source:ro" \
  alpine sh -c 'cp -a /source/. /target/'
```

Confirm the actual Compose volume name first with `docker volume ls`; the project-name prefix can differ when `COMPOSE_PROJECT_NAME` is set. Never run the copy while Armadillo is writing to either location.

Port allocations intentionally avoid overlap with the existing local stack (`8000`, `5432`, `8025`, `5555`).

### If Armadillo snapshot tables look empty

Run a manual backfill from HarmonAIze:

```bash
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell -c "from analysis.services import AnalysisExportService; print(AnalysisExportService().run_incremental_export())"
```

Then verify row counts in the snapshot database:

```bash
docker-compose -f docker-compose.local.yml exec -T armadillo_postgres psql -U debug -d armadillo_snapshot -c "SELECT 'harmonised_studies_snapshot' AS table_name, COUNT(*) FROM harmonised_studies_snapshot UNION ALL SELECT 'harmonised_attributes_snapshot', COUNT(*) FROM harmonised_attributes_snapshot UNION ALL SELECT 'harmonised_mapping_schemas_snapshot', COUNT(*) FROM harmonised_mapping_schemas_snapshot UNION ALL SELECT 'harmonised_mapping_rules_snapshot', COUNT(*) FROM harmonised_mapping_rules_snapshot UNION ALL SELECT 'harmonised_source_datasets_snapshot', COUNT(*) FROM harmonised_source_datasets_snapshot;"
```

### Armadillo compose port overrides

The vendored Armadillo compose files now default to non-overlapping host ports and support overrides via environment variables:

- `ARMADILLO_HOST_PORT` (default: `8001` for quickstart, `8081` for root/ci compose)
- `ARMADILLO_RSERVER_HOST_PORT` (default: `6312`)
- `ARMADILLO_KEYCLOAK_HOST_PORT` (default: `8080`)
- `ARMADILLO_KEYCLOAK_MANAGEMENT_HOST_PORT` (default: `9001`)

Example:

```bash
ARMADILLO_HOST_PORT=18081 docker-compose -f vendor/molgenis-service-armadillo/docker/quickstart/docker-compose.yml up -d
```

## Setup

**This application runs entirely in Docker containers. You don't need to install Python packages locally.**

### Environment Files

The env files committed under `./.envs/` are sanitised templates for onboarding and deployment reference.
They do not contain working credentials.

`./.envs/.local/.django` is the exception: it is gitignored and not committed.

**You don't have to edit secrets by hand.** On first start, `just build` / `just up` run `scripts/init_envs.py`, which sets them up for you. For a docker-compose production deployment, run `just init-production` once to fill `.envs/.production/*` as well.

It works like cookiecutter-django's first-run generation:

- It creates `./.envs/.local/.django` if it is missing.
- It replaces empty or `replace-with-...` values of secret settings with strong random values.
- It never overwrites a value you have already set, so it is safe to rerun.
- It marks tracked templates it changed as `skip-worktree`, so the generated secrets are not committed.
- It leaves PostgreSQL credentials alone if a database volume already exists, because PostgreSQL only creates its user on first start.
- It prints the names of what it changed, never the values, and lists the settings you still need to fill in.

The Azure deployment does not use these files. Terraform generates the same secrets with `random_password` and stores them in Key Vault (see `terraform/DEPLOY_RUNBOOK.md`).

#### What is automatic and what you must set

| Setting | Local | Production |
| --- | --- | --- |
| `DJANGO_SECRET_KEY`, `DJANGO_ADMIN_URL`, Flower user/password | Generated by the script | Generated by the script or Terraform |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | Generated by the script on a fresh volume | Generated by the script or Terraform |
| `ANALYSIS_DEIDENTIFICATION_SALT` | Fixed dev value from `docker-compose.local.yml` | Generated by the script or Terraform. If unset, it is derived from `DJANGO_SECRET_KEY`; set it explicitly so that rotating the key doesn't change pseudonyms |
| `ANALYSIS_ARMADILLO_PASSWORD`, `ANALYSIS_KEYCLOAK_ADMIN_PASSWORD` | `admin` (local containers only) | Generated by Terraform. With docker-compose, **you must set them**: Django shows warning `harmonaize.W001` while they are still `admin` |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_ACCOUNT_ALLOWED_EMAIL_DOMAINS` | Not needed | **Set** to your domain and the email domains allowed to register (default `witsphr.org,witsphr.co.za`; superusers can add more in the admin) |
| `OPENAI_API_KEY`, `MAPBOX_ACCESS_TOKEN`, `SENDGRID_API_KEY`, Azure storage | **Set yourself** if you use the feature | **Set yourself** |

Keep credential files such as Google Earth Engine JSON keys in a local-only path, for example `./.envs/.local/gee-credentials.json`, and keep real secrets out of commits. The `gitleaks` pre-commit hook blocks most accidental commits.

### Prerequisites

- **Docker Desktop**: https://www.docker.com/products/docker-desktop/ (Required)
- **just**: https://github.com/casey/just (`brew install just`), used for the first start
- **Git**: To clone the repository
- **Code Editor**: Visual Studio Code recommended

### Setup Instructions

1. **Start Docker Desktop** on your system

2. **Clone the repository**:
   ```bash
   git clone https://github.com/drnicholasbrink/HarmonAIze.git HarmonAIzeToolkit
   cd HarmonAIzeToolkit/harmonaize
   ```

3. **First start: build and start the application**:
   ```bash
   just build
   just up
   ```
   This creates `./.envs/.local/.django` and generates the local secrets (see [Environment Files](#environment-files)). After the first start, use `just up` or `docker compose -f docker-compose.local.yml up -d` as normal.

   Then add the optional keys you need to `./.envs/.local/.django` and restart.

   Common optional local settings:
   ```bash
   ANALYSIS_ENABLED=false
   OPENAI_API_KEY=sk-your-key
   MAPBOX_ACCESS_TOKEN=pk.your-mapbox-token
   GOOGLE_APPLICATION_CREDENTIALS=/app/.envs/.local/gee-credentials.json
   ```

   For Google Earth Engine, download the service account JSON key and place it at `./.envs/.local/gee-credentials.json` or another local path mounted into the container, then set `GOOGLE_APPLICATION_CREDENTIALS` to the matching in-container path. See `climate/GEE_SETUP.md` for the full workflow.

   `ANALYSIS_ENABLED` is a Django application flag. When it is `false`, the app starts without registering the analysis module and `/analysis/` shows the fallback unavailable page.

4. **Choose whether to run the analysis module**:

   With `ANALYSIS_ENABLED=false` in `./.envs/.local/.django`, this starts the normal local stack without the analysis module.

   To enable analysis locally, you need both pieces below:

   1. Set `ANALYSIS_ENABLED=true` in `./.envs/.local/.django` so Django loads the analysis app.
   2. Start Docker Compose with the `analysis` profile so Armadillo, Keycloak, and R server are started.

   Example:
   ```bash
   COMPOSE_PROFILES=analysis docker compose -f docker-compose.local.yml up -d
   ```

   This split is deliberate. `ANALYSIS_ENABLED` controls Django inside the container, while `COMPOSE_PROFILES=analysis` controls which Docker services start outside the container. The Django env file cannot automatically turn on the Compose profile because Docker Compose resolves profiles before it loads the service `env_file` entries.

   If you want to turn analysis back off, reset `ANALYSIS_ENABLED=false` in `./.envs/.local/.django` and start without the profile:
   ```bash
   docker compose -f docker-compose.local.yml up -d
   ```

5. **Run initial setup**:
   ```bash
   # Generate and run database migrations (only 0001 migrations are committed)
   docker compose -f docker-compose.local.yml run --rm django python manage.py makemigrations
   docker compose -f docker-compose.local.yml run --rm django python manage.py migrate
   
   # Create a superuser account
   docker compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser
   ```

6. **Access the application** at http://localhost:8000

### Module-specific setup

#### OpenAI

The transformation suggestion features rely on the OpenAI Responses API.

1. Copy your key from https://platform.openai.com/account/api-keys
2. Add it to `./.envs/.local/.django`:
   ```bash
   OPENAI_API_KEY=sk-your-key
   ```

#### Mapbox

Interactive map visualisations in the geolocation module require a Mapbox access token.

1. Create or copy a token from https://account.mapbox.com/access-tokens/
2. Add it to `./.envs/.local/.django`:
   ```bash
   MAPBOX_ACCESS_TOKEN=pk.your-mapbox-token
   ```
3. Restart the Django services after changing environment variables:
   ```bash
   docker compose -f docker-compose.local.yml restart django celeryworker celerybeat flower
   ```

#### Geolocation

Geolocation is scoped to a project (and optionally one study), like the rest of HarmonAIze. Pick the project on the geolocation dashboard; "View all projects" shows everything you can see. To geocode locations without a health dataset, use the geolocation-only workflow: upload a location CSV from the dashboard, and each upload becomes a location list in an existing project or a new "Geolocation only" project.

On first start the Django container downloads and indexes reference data and admin boundaries in the background, so admin-boundary geocoding may not be available for the first few minutes.

Optional geolocation settings in `./.envs/.local/.django`:

```bash
GOOGLE_GEOCODING_API_KEY=your-google-geocoding-key
GEOLOCATION_USE_LLM=true          # LLM-assisted matching; needs one of the keys below
GEMINI_API_KEY=your-gemini-key
ANTHROPIC_API_KEY=your-anthropic-key
```

#### Google Earth Engine

The climate module uses live Google Earth Engine data and requires credentials before climate requests will succeed.

1. Follow `climate/GEE_SETUP.md` to create a service account and download the JSON key
2. Save the file locally, for example at `./.envs/.local/gee-credentials.json`
3. Add the matching path to `./.envs/.local/.django`:
   ```bash
   GOOGLE_APPLICATION_CREDENTIALS=/app/.envs/.local/gee-credentials.json
   ```
4. Restart the services after updating the environment file:
   ```bash
   docker compose -f docker-compose.local.yml restart django celeryworker celerybeat flower
   ```

Repeat the same configuration pattern for other environments, such as `.envs/.production/.django` and `.envs/.production/.postgres`, before deploying.

For production, use the same rule: set `ANALYSIS_ENABLED=true` in `./.envs/.production/.django` only when the analysis module should be active, configure the production database credentials in `./.envs/.production/.postgres`, and start the production stack with the `analysis` profile only when the supporting analysis services are required.

**Note**: All dependencies are managed within Docker containers - no need to install Python packages locally!

## For LLM Agents: Development Guidelines

**This section provides essential guidelines for AI agents contributing to this codebase.**

### Core Principles

#### Critical Rules
1. **Database Models**: Only edit `models.py` files when **absolutely necessary**. Models are the foundation of the application and changes require careful consideration of:
   - Data migration implications
   - Existing data integrity
   - Downstream dependencies
   - **Always clearly document when and why model changes are made**

2. **Dependency Management**: When making any change, always consider and update related components:
   - URLs → Views → Templates → Forms
   - Models → Migrations → Admin → Serializers
   - JavaScript → CSS → HTML templates
   - Tests for all modified components

3. **Docker-First Development**: This is a containerized application:
   - All commands must use Docker containers
   - Never suggest installing packages locally
   - Always use `docker-compose -f docker-compose.local.yml` prefix
   - Test changes within the Docker environment

#### Design Philosophy
- **Cupertino Design System**: Follow Apple's design principles
  - Clean, minimal interfaces
  - Subtle animations and transitions
  - Consistent spacing and typography
  - Native-feeling interactions
  - Clear visual hierarchy

#### UI/UX Guidelines
- **No Emojis**: Avoid emojis in UI text, labels, or messages unless:
  - Used as functional icons (file folder for file, magnifying glass for search), but ideally use clean mono outlines. 
  - Part of status indicators where they add clarity
  - Explicitly requested by the user
- **Professional Tone**: Maintain a clean, professional interface

#### Feature Development
- **Full-Stack Implementation**: Always build complete features:
  - Backend models and business logic
  - API endpoints and views
  - Frontend templates and interactions
  - Form handling and validation
  - Error handling and user feedback
  - **Never deliver frontend-only implementations**

  #### Language
  - Always use british english spelling, except for HarmonAIze the Proper Noun. 



#### Project Structure Understanding
This is a **Cookiecutter Django** project with specific conventions:

```
harmonaize/
├── config/           # Django settings and configuration
├── core/            # Main application logic
├── health/          # Health data specific functionality  
├── climate/         # Climate data functionality
├── geolocation/     # Geographic data handling
├── analysis/        # DataSHIELD/Armadillo export (enabled by ANALYSIS_ENABLED)
├── harmonaize/      # Project utilities and shared components
├── templates/       # Global templates
├── requirements/    # Dependency specifications
└── compose/         # Docker configuration
```

#### Development Workflow
1. **Analyse Impact**: Before any change, identify all affected components
2. **Plan Dependencies**: Map out what needs updating (views, URLs, templates, tests)
3. **Implement Systematically**: Make changes in logical order
4. **Test Thoroughly**: Verify functionality in Docker environment
5. **Document Changes**: Explain what was changed and why

#### Specific Guidelines

**When editing Views:**
- Update corresponding URL patterns
- Ensure template context is complete
- Add proper error handling
- Consider pagination for list views
- Implement proper authentication checks

**When editing Templates:**
- Maintain consistent styling with existing templates
- Ensure responsive design
- Add proper CSRF tokens for forms
- Include loading states and error messages
- Follow accessibility best practices

**When editing Forms:**
- Add proper validation
- Include helpful error messages
- Consider user experience for field ordering
- Implement proper form widgets
- Add client-side validation where appropriate

**When editing Models:**
- **Ask first** - explain why the model change is necessary
- Plan migration strategy
- Consider backward compatibility
- Update related admin configurations
- Update any serializers or forms
- Add appropriate indexes and constraints

**When adding Features:**
- Create comprehensive tests
- Add proper documentation
- Consider internationalization
- Implement proper logging
- Add management commands if needed
- Consider performance implications

#### Testing Requirements
- Write tests for all new functionality
- Update existing tests when changing behaviour
- Test error conditions and edge cases
- Verify responsive design on different screen sizes
- Test with realistic data volumes

#### Documentation Standards
- Update README if adding new setup steps
- Document new environment variables
- Add docstrings to new functions and classes
- Update API documentation for new endpoints
- Include usage examples for complex features

#### Code Quality
- Follow PEP 8 style guidelines
- Use meaningful variable and function names
- Keep functions focused and single-purpose
- Add type hints where appropriate
- Remove unused imports and code
- Optimise database queries (avoid N+1 problems)

### Before Making Changes
1. **Read the codebase** to understand existing patterns
2. **Check for similar implementations** elsewhere in the project
3. **Identify all components** that need updating
4. **Plan the implementation** to ensure completeness
5. **Consider the user experience** from start to finish

### Common Pitfalls to Avoid
- **Avoid:** Making UI changes without backend support
- **Avoid:** Forgetting to update URL patterns after view changes
- **Avoid:** Adding forms without proper validation
- **Avoid:** Creating models without considering migrations
- **Avoid:** Implementing features without error handling
- **Avoid:** Using development shortcuts in production code
- **Avoid:** Breaking existing functionality while adding new features

**Remember**: This codebase serves researchers working with sensitive health and climate data. Reliability, security, and user experience are paramount.

## Docker Usage Guide

**Important**: This application is designed to run entirely within Docker containers. All Django management commands, database operations, and development tasks should be executed using Docker to ensure consistent environments and avoid dependency conflicts.

### Container Architecture

The application uses multiple Docker containers:

- **django**: Main Django application server
- **postgres**: PostgreSQL database
- **redis**: Redis cache and message broker
- **celeryworker**: Background task processor
- **celerybeat**: Periodic task scheduler
- **mailpit**: Local email testing server
- **flower**: Celery monitoring dashboard

### Essential Docker Commands

#### Starting the Application

```bash
# Start all containers in detached mode
docker-compose -f docker-compose.local.yml up -d

# Start with logs visible (useful for debugging)
docker-compose -f docker-compose.local.yml up

# Stop all containers
docker-compose -f docker-compose.local.yml down
```

#### Running Django Commands

**Always use the Django container for management commands:**

```bash
# Run database migrations
docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate

# Create database migrations
docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations

# Create superuser
docker-compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser

# Collect static files
docker-compose -f docker-compose.local.yml run --rm django python manage.py collectstatic

# Open Django shell
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell

# Run tests
docker-compose -f docker-compose.local.yml run --rm django pytest

# Install Python packages (when requirements change)
docker-compose -f docker-compose.local.yml build django
```

#### Database Operations

```bash
# Access PostgreSQL directly
docker-compose -f docker-compose.local.yml exec postgres psql -U debug -d harmonaize

# Create database backup
docker-compose -f docker-compose.local.yml exec postgres pg_dump -U debug harmonaize > backup.sql

# Restore database from backup
docker-compose -f docker-compose.local.yml exec -T postgres psql -U debug harmonaize < backup.sql
```

#### Viewing Logs

```bash
# View all container logs
docker-compose -f docker-compose.local.yml logs

# View specific container logs
docker-compose -f docker-compose.local.yml logs django
docker-compose -f docker-compose.local.yml logs postgres

# Follow logs in real-time
docker-compose -f docker-compose.local.yml logs -f django
```

#### Container Management

```bash
# View running containers
docker-compose -f docker-compose.local.yml ps

# Restart specific container
docker-compose -f docker-compose.local.yml restart django

# Rebuild containers (when Dockerfile changes)
docker-compose -f docker-compose.local.yml build

# Remove all containers and volumes (complete reset)
docker-compose -f docker-compose.local.yml down -v
```

### Development Workflow

1. **Start Development Environment**:
   ```bash
   docker-compose -f docker-compose.local.yml up -d
   ```

2. **Apply Database Changes**:
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations
   docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate
   ```

3. **Access Application**:
   - Main application: http://localhost:8000
   - Admin interface: http://localhost:8000/admin
   - Email testing: http://localhost:8025
   - Celery monitoring: http://localhost:5555

4. **Make Code Changes**: 
   Edit files normally - Django auto-reloads within the container

5. **Run Tests**:
   ```bash
   docker-compose -f docker-compose.local.yml run --rm django pytest
   ```

6. **Stop Development Environment**:
   ```bash
   docker-compose -f docker-compose.local.yml down
   ```

### Troubleshooting

#### Container Issues
```bash
# Check container status
docker-compose -f docker-compose.local.yml ps

# View container logs for errors
docker-compose -f docker-compose.local.yml logs django

# Restart problematic container
docker-compose -f docker-compose.local.yml restart django
```

#### Database Issues
```bash
# Reset database completely
docker-compose -f docker-compose.local.yml down -v
docker-compose -f docker-compose.local.yml up -d
docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate
```

#### Port Conflicts
If you get port binding errors, check for conflicting services:
```bash
# Check what's using port 8000
lsof -i :8000

# Kill process using the port
kill -9 <PID>
```

### Important Notes

- **Never install packages directly**: Always rebuild containers when dependencies change
- **Use container commands**: Don't run Django commands directly on your host system
- **Volume mounts**: Your code changes are automatically reflected due to volume mounts
- **Environment variables**: Managed through docker-compose files and .env files
- **Data persistence**: Database data persists in Docker volumes even when containers restart

## Settings

Moved to [settings](https://cookiecutter-django.readthedocs.io/en/latest/1-getting-started/settings.html).

## Basic Commands

### Setting Up Your Users

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Go to your console to see a simulated email verification message. Copy the link into your browser. Now the user's email should be verified and ready to go.

- To create a **superuser account**, use this command:

      $ docker-compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser

- Open your browser and go to: http://localhost:8000/admin
- Log in and start using the application

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or similar), so that you can see how the site behaves for both kinds of users.

### Reviewing the Entity-Relationship Diagram

To create and view the current ERD for all apps:

    $ docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models -a -o erd.png 

To only view one app's ERD, specify with app flag:

    $ docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models core health -o erd.png 

### Generating Entity-Relationship Diagrams (ERDs)

To generate ERDs for your Django models using `django-extensions` and `pygraphviz`, use the following commands. All output files will be saved in the `erd/` directory at the project root.

#### 1. Generate an overall ERD for all apps

```bash
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models -a -g -o erd/overall.png
```
- `-a` : Include all applications
- `-g` : Group models by app
- `-o` : Output file (format inferred from extension)

#### 2. Generate an ERD for a specific app (e.g. core)

```bash
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models core -g -o erd/core.png
```

#### 3. Generate ERDs for all main apps (core, health, climate, geolocation)

```bash
# Core app
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models core -g -o erd/core.png
# Health app
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models health -g -o erd/health.png
# Climate app
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models climate -g -o erd/climate.png
# Geolocation app
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models geolocation -g -o erd/geolocation.png
```

You can open the resulting PNG files in the `erd/` directory to view the diagrams. For other formats, change the file extension (e.g. `erd/overall.svg`).

> **Tip:** For more options, run:
> ```bash
> docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models --help
> ```

### Type Checks

Running type checks with mypy:

    $ docker-compose -f docker-compose.local.yml run --rm django mypy harmonaize

### Test Coverage

To run the tests, check your test coverage, and generate an HTML coverage report:

    $ docker-compose -f docker-compose.local.yml run --rm django coverage run -m pytest
    $ docker-compose -f docker-compose.local.yml run --rm django coverage html
    $ open htmlcov/index.html

#### Running Tests with pytest

    $ docker-compose -f docker-compose.local.yml run --rm django pytest

Two local prerequisites:

- The `analysis_export` database alias defaults to the `armadillo_postgres` host, which only exists in production. Point it at your local database for tests:
  ```bash
  docker compose -f docker-compose.local.yml run --rm django \
    sh -c 'ANALYSIS_EXPORT_DATABASE_URL="${DATABASE_URL%/*}/analysis_export" pytest'
  ```
- Test databases need the pgvector extension. If tests fail with `type "vector" does not exist`, enable it in the template database once:
  ```bash
  docker compose -f docker-compose.local.yml exec postgres sh -c 'psql -U "$POSTGRES_USER" -d template1 -c "CREATE EXTENSION IF NOT EXISTS vector;"'
  ```

About 20 climate tests currently fail on `main` because they are stale; see [#48](https://github.com/drnicholasbrink/HarmonAIze/issues/48).

### Live reloading and Sass CSS compilation

Moved to [Live reloading and SASS compilation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally.html#using-webpack-or-gulp).

### Celery

This app comes with Celery for background task processing.

**Note**: When using Docker, Celery workers run automatically as separate containers. You can monitor them via:

- **Flower Dashboard**: http://localhost:5555 (Celery monitoring interface)
- **Container logs**: `docker-compose -f docker-compose.local.yml logs celeryworker`

To run a celery worker manually (if needed):

```bash
docker-compose -f docker-compose.local.yml run --rm django celery -A config.celery_app worker -l info
```

Please note: For Celery's import magic to work, it is important _where_ the celery commands are run. Always use the Django container context.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), the celery beat scheduler runs automatically in the `celerybeat` container. To run it manually:

```bash
docker-compose -f docker-compose.local.yml run --rm django celery -A config.celery_app beat
```

or you can embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
docker-compose -f docker-compose.local.yml run --rm django celery -A config.celery_app worker -B -l info
```

### Email Server

In development, it is often nice to be able to see emails that are being sent from your application. For that reason local SMTP server [Mailpit](https://github.com/axllent/mailpit) with a web interface is available as docker container.

Container mailpit will start automatically when you will run all docker containers.
Please check [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally-docker.html) for more details how to start all containers.

With Mailpit running, to view messages that are sent by your application, open your browser and go to `http://localhost:8025`

### Sentry

Sentry is an error logging aggregator service. You can sign up for a free account at <https://sentry.io/signup/?code=cookiecutter> or download and host it yourself.
The system is set up with reasonable defaults, including 404 logging and integration with the WSGI application.

You must set the DSN url in production.

## Contributing and issue workflow

Work is tracked in [GitHub Issues](https://github.com/drnicholasbrink/HarmonAIze/issues). The pinned [roadmap issue (#64)](https://github.com/drnicholasbrink/HarmonAIze/issues/64) lists open work in priority order.

Every issue carries three kinds of label:

| Label | Meaning |
| --- | --- |
| `P0-critical` | Data loss, crash on a core path, or broken setup. Fix first. |
| `P1-high` | A main workflow is broken or wrong for typical use. |
| `P2-medium` | Degraded behaviour, a workaround exists, or a noticeable performance problem. |
| `P3-low` | Polish, tech debt, small UX issues. |
| `area: core`, `area: health`, `area: climate`, `area: geolocation`, `area: analysis`, `area: infra` | The module affected. |
| `bug`, `enhancement`, `performance`, `documentation`, `type: tests`, `tech-debt` | The kind of work. |

To pick something up:

1. Start with `good first issue` if you are new, otherwise take the highest-priority unassigned issue in your area.
2. Assign yourself, branch from `dev` (for example `fix/123-short-name`), and keep the PR to that one issue.
3. Reference the issue in the PR (`Fixes #123`), add or update tests, and run the affected tests locally (see [Running Tests with pytest](#running-tests-with-pytest)).
4. Do not commit generated migrations beyond `0001`, or anything under `.envs/`.

Security problems are never filed as public issues. See [SECURITY.md](SECURITY.md).

## Deployment

The following details how to deploy this application.
#TO DO

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).
