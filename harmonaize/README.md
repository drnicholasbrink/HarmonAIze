# HarmonAIze

An open-source toolkit that streamlines the process of harmonising and integrating climate and health data. Designed for research teams in resource-constrained settings, it leverages AI-assisted workflows to map, clean, and unify heterogeneous datasets, all while preserving privacy through federated approaches. By aligning health data with curated climate data sources, HarmonAIze accelerates climate–health research and enables faster, more accurate analyses of environmental impacts on health outcomes.

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

License: MIT

## Quick Start

**TL;DR**: This project runs entirely in Docker containers. Here's how to get started:

1. **Install Docker Desktop**: https://www.docker.com/products/docker-desktop/
2. **Clone and navigate**: Clone this repository and navigate to the `harmonaize` directory
3. **Start the application**:
   ```bash
   docker-compose -f docker-compose.local.yml up -d
   ```
4. **Make and run database migrations**:
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

**Important**: No need to install Python packages locally - everything runs in Docker containers!

## Setup

**This application runs entirely in Docker containers. You don't need to install Python packages locally.**

### Prerequisites

- **Docker Desktop**: https://www.docker.com/products/docker-desktop/ (Required)
- **Git**: To clone the repository
- **Code Editor**: Visual Studio Code recommended

### Setup Instructions

1. **Start Docker Desktop** on your system

2. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd harmonaize
   ```

3. **Build and start the application**:
   ```bash
   docker-compose -f docker-compose.local.yml up -d
   ```

4. **Run initial setup**:
   ```bash
   # Run database migrations
   docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate
   
   # Create a superuser account
   docker-compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser
   ```

5. **Access the application** at http://localhost:8000

### Configure OpenAI access

The transformation suggestion features rely on the OpenAI Responses API. Set an OpenAI API key before starting the containers:

1. Copy your key from https://platform.openai.com/account/api-keys
2. Add it to the local Django environment file `./.envs/.local/.django`:
   ```bash
   echo "OPENAI_API_KEY=sk-your-key" >> ./.envs/.local/.django
   ```
3. Restart the Django services so the environment variable is picked up:
   ```bash
   docker-compose -f docker-compose.local.yml restart django celeryworker celerybeat flower
   ```

Repeat the same configuration for other environments (e.g. `.envs/.production/.django`) before deploying.

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

## Deployment

The following details how to deploy this application.
#TO DO

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).
