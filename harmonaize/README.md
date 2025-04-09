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

## Deployment

The following details how to deploy this application.

### Docker

See detailed [cookiecutter-django Docker documentation](https://cookiecutter-django.readthedocs.io/en/latest/3-deployment/deployment-with-docker.html).
