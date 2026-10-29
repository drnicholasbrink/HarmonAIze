# define an alias for the specific python version used in this file.
FROM docker.io/python:3.12.16-slim-bookworm AS python

# eMudhra/emSign CA incident: pip trusts both the system CA store and its own
# vendored certifi bundle. Remove the compromised root from the system store
# first, then install pip 26.2.2 from a hash-pinned URL.
# https://discuss.python.org/t/109576
RUN apt-get update && apt-get install --no-install-recommends -y --only-upgrade ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && python -m pip install --no-cache-dir \
  "pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"


# Python build stage
FROM python AS python-build-stage

ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg dependencies
  libpq-dev \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# Requirements are installed here to ensure they will be cached.
COPY ./requirements /requirements

# create python dependency wheels
RUN pip wheel --no-cache-dir --wheel-dir /usr/src/app/wheels  \
  -r /requirements/local.txt -r /requirements/production.txt \
  && rm -rf /requirements


# Python 'run' stage
FROM python AS python-run-stage

ARG BUILD_ENVIRONMENT
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install --no-install-recommends -y \
  # To run the Makefile
  make \
  # psycopg dependencies
  libpq-dev \
  # Translations dependencies
  gettext \
  # Uncomment below lines to enable Sphinx output to latex and pdf
  # texlive-latex-recommended \
  # texlive-fonts-recommended \
  # texlive-latex-extra \
  # latexmk \
  # cleaning up unused files
  && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
  && rm -rf /var/lib/apt/lists/*

# copy python dependency wheels from python-build-stage
COPY --from=python-build-stage /usr/src/app/wheels /wheels

# use wheels to install python dependencies
RUN pip install --no-cache /wheels/* \
  && rm -rf /wheels

COPY ./compose/local/docs/start /start-docs
RUN sed -i 's/\r$//g' /start-docs
RUN chmod +x /start-docs

WORKDIR /docs
