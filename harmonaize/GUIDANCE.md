# GUIDANCE.md

This file provides development guidance and essential commands for working with the HarmonAIze codebase.

## Project Overview

HarmonAIze is an open-source toolkit for harmonising and integrating climate and health data, built with Django and running entirely in Docker containers. The project uses a Cookiecutter Django template structure with AI-assisted workflows for data mapping and unification.

## Essential Commands

All commands must be run using Docker. Never install packages locally or run Django commands directly on the host system.

### Starting Development Environment
```bash
# Start all containers
docker-compose -f docker-compose.local.yml up -d

# Stop containers
docker-compose -f docker-compose.local.yml down

# View logs
docker-compose -f docker-compose.local.yml logs -f django
```

### Django Management Commands
```bash
# Run migrations
docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate

# Create migrations
docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations

# Create superuser
docker-compose -f docker-compose.local.yml run --rm django python manage.py createsuperuser

# Django shell
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell

# Collect static files
docker-compose -f docker-compose.local.yml run --rm django python manage.py collectstatic
```

### Testing and Code Quality
```bash
# Run tests
docker-compose -f docker-compose.local.yml run --rm django pytest

# Run specific test
docker-compose -f docker-compose.local.yml run --rm django pytest path/to/test.py::TestClass::test_method

# Test coverage
docker-compose -f docker-compose.local.yml run --rm django coverage run -m pytest
docker-compose -f docker-compose.local.yml run --rm django coverage html

# Type checking
docker-compose -f docker-compose.local.yml run --rm django mypy harmonaize

# Linting with ruff
docker-compose -f docker-compose.local.yml run --rm django ruff check .
docker-compose -f docker-compose.local.yml run --rm django ruff format .

# Template linting with djlint
docker-compose -f docker-compose.local.yml run --rm django djlint . --check
docker-compose -f docker-compose.local.yml run --rm django djlint . --reformat
```

### Generate ERD Diagrams
```bash
# Generate overall ERD
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models -a -g -o erd/overall.png

# Generate app-specific ERD
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models core -g -o erd/core.png
```

## Architecture Overview

### Project Structure
```
harmonaize/
├── config/           # Django settings, URLs, WSGI/ASGI configuration
│   ├── settings/     # Base, local, production, test settings
│   ├── urls.py       # Main URL configuration
│   └── celery_app.py # Celery configuration
├── core/            # Main application logic, project management
├── health/          # Health data functionality
├── climate/         # Climate data functionality  
├── geolocation/     # Geographic data handling
├── harmonaize/      # Project-wide utilities
│   ├── users/       # User authentication and management
│   ├── static/      # Static files (CSS, JS, images)
│   └── templates/   # Global templates
├── compose/         # Docker configuration files
└── requirements/    # Python dependencies (base, local, production)
```

### Key Architectural Patterns

1. **Docker-First Development**: All services run in containers (Django, PostgreSQL, Redis, Celery, Mailpit)

2. **Django Apps Organisation**:
   - `core`: Central project and study management, orchestrates harmonisation workflows
   - `health`: Health data models, variable extraction, codebook mapping
   - `climate`: Climate data integration and processing
   - `geolocation`: Geographic data handling and mapping
   - `users`: Custom user model with allauth integration

3. **Authentication**: Django-allauth with MFA support, custom user model in `harmonaize.users`

4. **Background Tasks**: Celery with Redis broker for async processing, monitored via Flower

5. **Database**: PostgreSQL with migrations managed per app

6. **API**: Django REST Framework with drf-spectacular for OpenAPI documentation

7. **Frontend**: Server-side rendered templates with Bootstrap 5, Crispy Forms for form rendering

## Critical Development Rules

### Database Models
- **NEVER edit models.py without careful consideration** - changes affect migrations, data integrity, and dependencies
- Always create migrations after model changes
- Test migration rollback scenarios

### British English
- Use British English spelling throughout (except for "HarmonAIze" proper noun)
- Examples: harmonise (not harmonize), colour (not color), centre (not center)

### UI/UX Guidelines
- Follow Cupertino/Apple design principles - clean, minimal, professional
- No emojis in UI text unless explicitly requested
- Maintain consistent spacing, typography, and visual hierarchy

### Full-Stack Implementation
- Always implement complete features: backend → API → frontend → tests
- Never deliver frontend-only implementations
- Update all related components: URLs → Views → Templates → Forms

### Docker Usage
- All commands use `docker-compose -f docker-compose.local.yml`
- Never suggest local package installation
- Test all changes within Docker environment

## Service URLs

- **Application**: http://localhost:8000
- **Admin Panel**: http://localhost:8000/admin
- **Email Testing (Mailpit)**: http://localhost:8025
- **Celery Monitoring (Flower)**: http://localhost:5555

## Common Development Workflows

### Adding a New Feature
1. Plan the data model (if needed)
2. Create/update models and migrations
3. Update admin configuration
4. Create views and URL patterns
5. Design templates with Bootstrap 5
6. Add forms with validation
7. Write comprehensive tests
8. Update documentation

### Modifying Existing Components
1. Identify all dependencies
2. Update in logical order (models → views → templates)
3. Ensure backward compatibility
4. Update related tests
5. Run linting and type checking

### Debugging
```bash
# Access Django shell
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell

# View container logs
docker-compose -f docker-compose.local.yml logs -f django

# Access PostgreSQL
docker-compose -f docker-compose.local.yml exec postgres psql -U debug -d harmonaize
```

## Testing Strategy

- Unit tests for models, forms, utilities
- Integration tests for views and API endpoints
- Use factory-boy for test data generation
- Maintain test coverage above 80%
- Test error conditions and edge cases

## Important Notes

- Settings are environment-specific (base.py, local.py, production.py, test.py)
- Static files served from `harmonaize/static/` in development
- Media files not currently configured (add if needed)
- Celery tasks defined per app in `tasks.py`
- API serializers in `app_name/api/serializers.py`
- Custom user model requires AUTH_USER_MODEL setting