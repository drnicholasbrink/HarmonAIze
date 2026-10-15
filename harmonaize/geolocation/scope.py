"""
Project / study scoping for the geolocation module.

Mirrors the rest of HarmonAIze: work happens inside a project, optionally
narrowed to one source or target study. "All projects" is available as an
explicit "view all" option.

The active scope is read from ``?project=<id|all>&study=<id>``. Pages and the
dashboard's API calls pass it explicitly; the last explicit selection is also
remembered in the session as a fallback for links and calls that don't.
"""
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from core.models import Project, Study

SESSION_KEY = "geolocation_scope"
ALL_PROJECTS = "all"


@dataclass
class GeolocationScope:
    project: Optional[Project] = None
    study: Optional[Study] = None

    @property
    def is_all(self):
        return self.project is None

    @property
    def label(self):
        if self.is_all:
            return "All projects"
        if self.study is not None:
            return f"{self.project.name} / {self.study.name}"
        return self.project.name

    @property
    def params(self):
        if self.is_all:
            return {"project": ALL_PROJECTS}
        params = {"project": self.project.pk}
        if self.study is not None:
            params["study"] = self.study.pk
        return params

    @property
    def querystring(self):
        return urlencode(self.params)


def projects_for_user(user):
    return Project.objects.filter(members=user).distinct().order_by("-updated_at")


def _parse_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def resolve_scope(request, remember=True):
    """Return the active GeolocationScope for this request.

    Precedence: explicit query parameters, then the session, then the user's
    most recently updated project (or "all" if they have no projects).
    """
    projects = projects_for_user(request.user)

    if "project" in request.GET:
        raw_project = request.GET.get("project")
        raw_study = request.GET.get("study")
    else:
        stored = request.session.get(SESSION_KEY) or {}
        raw_project = stored.get("project")
        raw_study = stored.get("study")

    if raw_project == ALL_PROJECTS:
        project = None
    else:
        project_id = _parse_id(raw_project)
        project = projects.filter(pk=project_id).first() if project_id else None
        if project is None:
            project = projects.first()

    study = None
    if project is not None:
        study_id = _parse_id(raw_study)
        if study_id:
            study = project.studies.filter(pk=study_id).first()

    scope = GeolocationScope(project=project, study=study)
    if remember and "project" in request.GET:
        # Only an explicit selection is remembered; polling APIs don't write the session.
        selection = {
            "project": ALL_PROJECTS if project is None else project.pk,
            "study": study.pk if study else None,
        }
        if request.session.get(SESSION_KEY) != selection:
            request.session[SESSION_KEY] = selection
    return scope
