# HarmonAIze AI Agent Instructions

HarmonAIze is a Django-based research toolkit that streamlines harmonizing and integrating climate and health data for research teams in resource-constrained settings. Understanding the project architecture and development patterns is crucial for effective contributions.

## Project Architecture

### Core Domain Model
The application revolves around a multi-tiered data harmonisation workflow:

- **Projects** contain multiple **Studies** (both source and target studies)
- **Studies** define **Attributes** (variables) and upload **RawDataFiles** 
- **MappingSchemas** define transformations from source to target studies via **MappingRules**
- **Observations** store the actual data points linked to **Patients**, **Locations**, and **TimeDimensions**

**Critical Rule**: Never modify `models.py` files without explicit justification. Models are the foundation - changes require careful migration planning and impact analysis.

### Django App Structure
```
harmonaize/           # Project utilities and shared components
├── config/          # Django settings and main configuration  
├── core/            # Main domain models (Project, Study, Attribute, Observation)
├── health/          # Health-specific functionality (MappingSchema, RawDataFile)
├── climate/         # Climate data functionality (TBD)
├── geolocation/     # Geographic data handling (TBD)
└── harmonaize/users/ # Custom user management
```

## Development Workflow

### Docker-First Development
**Everything runs in containers** - never suggest installing packages locally:
```bash
# Essential commands (always use these patterns)
docker-compose -f docker-compose.local.yml up -d          # Start environment
docker-compose -f docker-compose.local.yml run --rm django python manage.py [command]
docker-compose -f docker-compose.local.yml logs django   # View logs
```

### Full-Stack Implementation Requirements
When implementing features, always build:
1. **Backend**: Models, views, URL patterns, forms, admin config
2. **Frontend**: Templates with Cupertino design system styling
3. **Data validation**: Form validation and model constraints
4. **Error handling**: User-friendly error messages and fallbacks
5. **Tests**: Unit tests for business logic

**Never deliver frontend-only changes** - every UI element must have proper backend support.

### Cupertino Design Guidelines
- Clean, minimal interfaces with subtle shadows and rounded corners (12px border-radius)
- Colour palette: Primary `#007AFF`, Secondary `#5AC8FA`, Success `#34C759`
- **No emojis** in UI text except functional icons (file folders, search magnifying glass)
- Typography: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- British English spelling throughout (except "HarmonAIze" proper noun)

## Key Technical Patterns

### File Upload Processing
Raw data files follow this workflow:
1. Upload to `RawDataFile` → validate format → extract column metadata
2. Map columns to study attributes via `RawDataColumn.mapped_variable`
3. Process data through `MappingSchema` transformations into `Observations`

Critical files:
- `health/models.py`: `RawDataFile`, `RawDataColumn` models
- `health/views.py`: Upload and processing views
- `health/templates/health/harmonization_dashboard.html`: Main UI

### Mapping Transformation System
Source study attributes → Target study attributes via safe Python transformations:
```python
# Example in MappingRule.transform_code
lambda value: value.upper().strip() if value else ""
```

Transformation validation enforces security through AST parsing (`validate_safe_transform_code`).

### Data Validation Architecture
Multi-layer validation:
1. **Model validation**: `clean()` methods enforce business rules
2. **Form validation**: User input sanitisation and feedback
3. **File validation**: Format detection and column mapping
4. **Transform validation**: Safe code execution constraints

### Context Processors
Key template context available globally:
- `target_study_context`: Provides target study info for mapping workflows
- `allauth_settings`: Authentication configuration

## Development Commands

### Database Operations
```bash
# Migrations (be extremely careful with model changes)
docker-compose -f docker-compose.local.yml run --rm django python manage.py makemigrations
docker-compose -f docker-compose.local.yml run --rm django python manage.py migrate

# ERD generation (helpful for understanding relationships)
docker-compose -f docker-compose.local.yml run --rm django python manage.py graph_models -a -g -o erd/overall.png
```

### Testing and Quality
```bash
# Run tests
docker-compose -f docker-compose.local.yml run --rm django pytest

# Type checking
docker-compose -f docker-compose.local.yml run --rm django mypy harmonaize

# Django shell for debugging
docker-compose -f docker-compose.local.yml run --rm django python manage.py shell
```

## Critical Integration Points

### Template Inheritance
All templates extend `base.html` which provides:
- Bootstrap 5 + Cupertino styling
- CSRF tokens and security headers
- Navigation and user authentication state
- Message framework for user feedback

### URL Patterns
Main URL namespaces:
- `core:` - Project and study management
- `health:` - Harmonisation workflows and data upload
- `users:` - Authentication and user management
- `admin:` - Django admin interface

### Celery Background Tasks
Background processing for:
- File upload validation and parsing
- Data transformation and harmonisation
- Large dataset processing

Monitor via Flower at `http://localhost:5555` in development.

## Common Pitfalls to Avoid

1. **Model Changes**: Never modify models without migration strategy
2. **Frontend-Only Features**: Always implement backend support first
3. **Direct Package Installation**: Everything must work in Docker
4. **Generic Error Handling**: Provide specific, actionable error messages
5. **Security Shortcuts**: All user input must be validated and sanitized
6. **Performance Issues**: Always consider database query optimization for large datasets

## File Locations for Common Tasks

- **New views**: Add to `app/views.py` + URL patterns in `app/urls.py`
- **Templates**: `app/templates/app/` following naming conventions
- **Forms**: `app/forms.py` with crispy-forms Bootstrap 5 styling
- **Models**: Only modify `app/models.py` when absolutely necessary
- **Static assets**: `harmonaize/static/` (CSS, JS, images)
- **Media uploads**: `harmonaize/media/` (user uploaded files)

The codebase serves researchers handling sensitive health and climate data - prioritise reliability, security, and user experience in all implementations.