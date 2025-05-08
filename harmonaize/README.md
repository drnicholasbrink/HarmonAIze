# HarmonAIze

an open-source toolkit that streamlines the process of harmonizing and integrating climate and health data. Designed for research teams in resource-constrained settings, it leverages AI-assisted workflows to map, clean, and unify heterogeneous datasets, all while preserving privacy through federated approaches. By aligning health data with curated climate data sources, HarmonAIze accelerates climate–health research and enables faster, more accurate analyses of environmental impacts on health outcomes.

[![Built with Cookiecutter Django](https://img.shields.io/badge/built%20with-Cookiecutter%20Django-ff69b4.svg?logo=cookiecutter)](https://github.com/cookiecutter/cookiecutter-django/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

License: MIT

## Setup


Prerequisites
-------------

Before you begin, ensure you have the following installed on your system:

- Python: https://www.python.org/downloads/
- pip: https://pip.pypa.io/en/stable/installation/
- Docker Desktop: https://www.docker.com/products/docker-desktop/
- conda or your preferred virtual environment manager: https://docs.conda.io/en/latest/miniconda.html

Setup Instructions
------------------

1. Start Your Environment

- Launch your virtual environment (e.g., using conda)
- Start Docker Desktop

2. Open the Project

- Use Visual Studio Code (https://code.visualstudio.com/) or your preferred editor
- Open a terminal inside the project root directory
- Ensure you’re using the correct Python interpreter from your virtual environment
- Clone this repo locally using git clone <url>

3. Install Dependencies

Make sure you are in the `harmonaize` project directory:

    cd harmonaize

Install Python dependencies:

    pip install -r requirements/base.txt
    pip install -r requirements/local.txt  # Note: 'psycopg' may cause issues
    pip install -r requirements/production.txt

4. Build and Run with Docker

    docker-compose -f docker-compose.local.yml build

Run Django database migrations:

    python manage.py makemigrations
    python manage.py migrate

Start the local server:

    docker-compose -f docker-compose.local.yml up



## Settings

Moved to [settings](https://cookiecutter-django.readthedocs.io/en/latest/1-getting-started/settings.html).

## Basic Commands

### Setting Up Your Users

- To create a **normal user account**, just go to Sign Up and fill out the form. Once you submit it, you'll see a "Verify Your E-mail Address" page. Go to your console to see a simulated email verification message. Copy the link into your browser. Now the user's email should be verified and ready to go.

- To create a **superuser account**, use this command:

      $ python manage.py createsuperuser
- Alternatively if your container is not running, use this command:
        $ docker-compose -f local.yml run --rm django python manage.py createsuperuser

- Open your browser and go to: http://127.0.0.1:8000/admin
- Log in and start using the application

For convenience, you can keep your normal user logged in on Chrome and your superuser logged in on Firefox (or similar), so that you can see how the site behaves for both kinds of users.

### Type checks

Running type checks with mypy:

    $ mypy harmonaize

### Test coverage

To run the tests, check your test coverage, and generate an HTML coverage report:

    $ coverage run -m pytest
    $ coverage html
    $ open htmlcov/index.html

#### Running tests with pytest

    $ pytest

### Live reloading and Sass CSS compilation

Moved to [Live reloading and SASS compilation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally.html#using-webpack-or-gulp).

### Celery

This app comes with Celery.

To run a celery worker:

```bash
cd harmonaize
celery -A config.celery_app worker -l info
```

Please note: For Celery's import magic to work, it is important _where_ the celery commands are run. If you are in the same folder with _manage.py_, you should be right.

To run [periodic tasks](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html), you'll need to start the celery beat scheduler service. You can start it as a standalone process:

```bash
cd harmonaize
celery -A config.celery_app beat
```

or you can embed the beat service inside a worker with the `-B` option (not recommended for production use):

```bash
cd harmonaize
celery -A config.celery_app worker -B -l info
```

### Email Server

In development, it is often nice to be able to see emails that are being sent from your application. For that reason local SMTP server [Mailpit](https://github.com/axllent/mailpit) with a web interface is available as docker container.

Container mailpit will start automatically when you will run all docker containers.
Please check [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/2-local-development/developing-locally-docker.html) for more details how to start all containers.

With Mailpit running, to view messages that are sent by your application, open your browser and go to `http://127.0.0.1:8025`

### Sentry

Sentry is an error logging aggregator service. You can sign up for a free account at <https://sentry.io/signup/?code=cookiecutter> or download and host it yourself.
The system is set up with reasonable defaults, including 404 logging and integration with the WSGI application.

You must set the DSN url in production.

## Contributing & Development Guidelines

### Project Overview

HarmonAIze is a Django-based web application designed to facilitate the harmonization of climate and health data. The project is structured as follows:

- **config/**: Contains Django project settings, URL configurations, and WSGI/ASGI applications
- **harmonaize/**: Main Django application with core functionality 
  - **templates/**: HTML templates for the web interface
  - **static/**: CSS, JavaScript, and static files
  - **users/**: User authentication and management
- **core/**: Contains data processing and mapping functionality

### Getting Started as a Contributor

1. **Fork the Repository**
   - Create your own fork of the repository on GitHub
   - Clone your fork locally

2. **Set Up Development Environment**
   - Follow the setup instructions above
   - Create a new branch for your feature or fix: `git checkout -b feature/your-feature-name`

3. **Understand the Architecture**
   - The application follows Django's MVT (Model-View-Template) pattern
   - Data mapping functionality is in the `core` app
   - User interface components are in the `harmonaize/templates` directory

### Development Workflow

1. **Pick an Issue or Feature**
   - Check the GitHub issues for open tasks
   - Discuss with the team if you want to implement a new feature

2. **Local Development**
   - Make your changes locally
   - Test thoroughly using Django's development server
   - Use Docker for a production-like environment

3. **Code Style and Standards**
   - Follow PEP 8 for Python code
   - Use the pre-configured ruff linter to check your code: `ruff check .`
   - Document your code using docstrings
   - Add type hints for better code readability

4. **Testing**
   - Write tests for your code using pytest
   - Ensure all tests pass: `pytest`
   - Aim for good test coverage

5. **Submitting Changes for Review**
   - Commit your changes with clear, descriptive messages
   - Push to your fork: `git push origin feature/your-feature-name`
   - Create a Pull Request (PR) against Dr. Nicholas Brink's original repository:
     1. Go to [the original repository](https://github.com/drnicholasbrink/HarmonAIze)
     2. Click on "Pull Requests" > "New Pull Request"
     3. Click "compare across forks"
     4. Select your fork as the head repository and your feature branch
     5. Click "Create Pull Request"
     6. Fill out the PR template with a detailed description of your changes
     7. Tag relevant reviewers (e.g., @drnicholasbrink)
   - Respond to any feedback or requested changes from reviewers
   - Make additional commits to address review comments if needed
   - Once approved, your changes will be merged into the main repository

### Feature Development Guidelines

#### Adding a New Feature

1. **Plan Your Feature**
   - Define the scope and requirements
   - Create a design document if needed

2. **Implement Backend Logic**
   - Add models to represent your data
   - Create views in `core/views.py` or a new app if necessary
   - Add URL patterns in `config/urls.py`
   - Include necessary validation and error handling

3. **Develop Frontend Components**
   - Create or modify templates in `harmonaize/templates/`
   - Use Bootstrap for consistent styling
   - Test responsiveness on different devices

4. **Documentation**
   - Document your APIs and functions
   - Update the README or documentation as needed
   - Add usage examples where appropriate

#### Data Processing Features

When adding data processing features:

1. Use pandas for data manipulation (already installed)
2. Add proper error handling for file uploads and data processing
3. Implement async processing for resource-intensive tasks using Celery
4. Consider privacy implications when handling health data

### Running Tests and Validation

```bash
# Run all tests
pytest

# Run tests for a specific app
pytest core/

# Check code style
ruff check .

# Run type checking
mypy harmonaize
```

### Common Development Tasks

#### Adding a New Dependency

1. Add the dependency to the appropriate requirements file:
   - `requirements/base.txt` for core dependencies
   - `requirements/local.txt` for development dependencies
   - `requirements/production.txt` for production-specific dependencies

2. Rebuild your Docker container:
   ```bash
   docker-compose -f docker-compose.local.yml build
   ```

#### Creating a New Django App

```bash
python manage.py startapp new_app_name
```

Then add your app to `INSTALLED_APPS` in `config/settings/base.py`.

#### Database Migrations

After modifying models:

```bash
python manage.py makemigrations
python manage.py migrate
```

### Getting Help

- Join our community discussions on GitHub
- Check out the existing codebase for examples
- Reach out to the team via the project email or issue comments

## Deployment

The following details how to deploy this application.

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).
