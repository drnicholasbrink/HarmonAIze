import hashlib
from collections.abc import Callable

from django.core.cache import caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.middleware.csrf import get_token
from django.template.loader import render_to_string

DASHBOARD_FRAGMENT_CACHE_ALIAS = "dashboard_fragments"
DASHBOARD_FRAGMENT_TIMEOUT = 20 * 60
EDA_FRAGMENT_TIMEOUT = 6 * 60 * 60


def _cache():
    try:
        return caches[DASHBOARD_FRAGMENT_CACHE_ALIAS]
    except InvalidCacheBackendError:
        return caches["default"]


def _version_key(schema_id: int) -> str:
    return f"mapping-dashboard:v2:schema:{schema_id}:version"


def schema_dashboard_cache_version(schema_id: int) -> int:
    cache = _cache()
    version = cache.get(_version_key(schema_id))
    if not version:
        version = 1
        cache.set(_version_key(schema_id), version, timeout=None)
    return int(version)


def bump_schema_dashboard_cache_version(schema_id: int) -> int:
    cache = _cache()
    key = _version_key(schema_id)
    try:
        return int(cache.incr(key))
    except ValueError:
        cache.set(key, 2, timeout=None)
        return 2


def _safe_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def render_cached_card_fragment(
    request,
    *,
    schema_id: int,
    attribute_id: int,
    template_name: str,
    context: dict,
) -> str:
    cache = _cache()
    version = schema_dashboard_cache_version(schema_id)
    user_id = getattr(request.user, "id", None) or "anon"
    csrf_hash = _safe_hash(get_token(request))
    key = (
        f"mapping-card:v10:schema:{schema_id}:attr:{attribute_id}:"
        f"version:{version}:user:{user_id}:csrf:{csrf_hash}"
    )
    html = cache.get(key)
    if html is not None:
        return html
    html = render_to_string(template_name, context, request=request)
    cache.set(key, html, DASHBOARD_FRAGMENT_TIMEOUT)
    return html


def _raw_cache_fingerprint(raw_data_file) -> str:
    if not raw_data_file:
        return "none"
    timestamp = (
        getattr(raw_data_file, "eda_cache_source_generated_at", None)
        or getattr(raw_data_file, "updated_at", None)
        or getattr(raw_data_file, "uploaded_at", None)
    )
    timestamp_value = timestamp.isoformat() if timestamp else "undated"
    return f"{raw_data_file.id}:{timestamp_value}"


def render_cached_eda_fragment(
    *,
    request=None,
    schema_id: int,
    attribute_id: int,
    variable_name: str,
    raw_data_file,
    column_type: str | None,
    template_name: str,
    context: dict,
    renderer: Callable[[], str] | None = None,
) -> str:
    if not column_type:
        return render_to_string(template_name, context, request=request)

    cache = _cache()
    raw_fingerprint = _safe_hash(_raw_cache_fingerprint(raw_data_file))
    variable_hash = _safe_hash(variable_name.lower())
    key = (
        f"mapping-eda:v2:schema:{schema_id}:attr:{attribute_id}:"
        f"raw:{raw_fingerprint}:var:{variable_hash}"
    )
    html = cache.get(key)
    if html is not None:
        return html
    html = renderer() if renderer else render_to_string(template_name, context, request=request)
    cache.set(key, html, EDA_FRAGMENT_TIMEOUT)
    return html
