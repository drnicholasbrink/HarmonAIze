import base64
import json
import logging
import socket
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error
from urllib import parse
from urllib import request

import pandas as pd
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from django_celery_beat.models import IntervalSchedule
from django_celery_beat.models import PeriodicTask

from core.models import Observation
from core.models import Project
from health import dataset_exports

from .models import AnalysisDatasetTemplate
from .models import AnalysisScheduleConfiguration

logger = logging.getLogger(__name__)

TABLE_OBJECT_FILENAME = "data.parquet"
TABLE_RESOURCE_NAME = "data"


class AnalysisExportService:
    """Project sync service for Armadillo/Keycloak using structured Parquet datasets."""

    def __init__(self):
        self.deidentify_default = True
        self.export_root = Path(
            getattr(
                settings,
                "ANALYSIS_EXPORT_ROOT",
                str(Path(settings.MEDIA_ROOT) / "analysis_exports"),
            ),
        )
        self.armadillo_client = ArmadilloClient(
            base_url=getattr(
                settings,
                "ANALYSIS_ARMADILLO_BASE_URL",
                "http://armadillo:8080",
            ),
            username=getattr(settings, "ANALYSIS_ARMADILLO_USERNAME", "admin"),
            password=getattr(settings, "ANALYSIS_ARMADILLO_PASSWORD", "admin"),
        )
        self.keycloak_sync_enabled = bool(
            getattr(settings, "ANALYSIS_KEYCLOAK_SYNC_ENABLED", False),
        )
        self.keycloak_client = KeycloakAdminClient(
            base_url=getattr(
                settings,
                "ANALYSIS_KEYCLOAK_BASE_URL",
                "http://keycloak:8080",
            ),
            realm=getattr(settings, "ANALYSIS_KEYCLOAK_REALM", "Armadillo"),
            admin_username=getattr(
                settings,
                "ANALYSIS_KEYCLOAK_ADMIN_USERNAME",
                "admin",
            ),
            admin_password=getattr(
                settings,
                "ANALYSIS_KEYCLOAK_ADMIN_PASSWORD",
                "admin",
            ),
        )

    def run_project_sync(self, project: Project) -> dict[str, Any]:
        exported_at = timezone.now()
        armadillo_project = self._armadillo_project_name(project)

        synced_users_count = self._sync_project_members(project, armadillo_project)
        synced_projects_count = self._sync_armadillo_project(project, armadillo_project)

        templates = list(
            AnalysisDatasetTemplate.objects.filter(
                project=project,
                is_active=True,
            )
            .select_related("target_study", "target_study__project")
            .prefetch_related(
                "source_studies",
                "outcome_attributes",
                "confounder_attributes",
                "climate_attributes",
                "location_attributes",
            )
            .order_by("target_study__name", "name"),
        )
        parquet_files: list[Path] = []
        artifact_manifest: list[dict[str, Any]] = []
        uploaded_objects_count = 0
        processed_attributes = 0
        processed_target_studies = set()

        for template in templates:
            parquet_path, object_name, attribute_count, row_count, dataset_slug = self._write_dataset_parquet(
                project=project,
                dataset_template=template,
                exported_at=exported_at,
            )
            processed_attributes += attribute_count
            parquet_files.append(parquet_path)
            processed_target_studies.add(template.target_study_id)

            for legacy_object_name in self._legacy_object_names(
                template.target_study,
                dataset_slug,
                current_object_name=object_name,
            ):
                self.armadillo_client.delete_object(
                    project_name=armadillo_project,
                    object_name=legacy_object_name,
                )

            self.armadillo_client.replace_object(
                project_name=armadillo_project,
                object_name=object_name,
                file_path=parquet_path,
            )
            uploaded_objects_count += 1

            artifact_manifest.append(
                {
                    "template_id": template.id,
                    "template_name": template.name,
                    "target_study_id": template.target_study_id,
                    "target_study_name": template.target_study.name,
                    "armadillo_project": armadillo_project,
                    "dataset_shape": template.dataset_shape,
                    "dataset_slug": dataset_slug,
                    "object_name": object_name,
                    "object_names": [object_name],
                    "path": str(parquet_path),
                    "rows": row_count,
                },
            )

        return {
            "processed_studies": len(processed_target_studies),
            "processed_target_studies": len(processed_target_studies),
            "processed_attributes": processed_attributes,
            "generated_parquet_files": len(parquet_files),
            "uploaded_objects_count": uploaded_objects_count,
            "synced_users_count": synced_users_count,
            "synced_projects_count": synced_projects_count,
            "artifact_manifest": artifact_manifest,
            "watermark_after": exported_at,
        }

    def _sync_project_members(self, project: Project, armadillo_project: str) -> int:
        memberships = list(
            project.memberships.select_related("user").order_by("user__email"),
        )
        count = 0

        for membership in memberships:
            user = membership.user
            projects = [armadillo_project]
            user_payload = {
                "email": user.email,
                "firstName": getattr(user, "name", "") or "",
                "lastName": "",
                "institution": getattr(user, "organization", "") or "",
                "admin": bool(user.is_superuser),
                "projects": projects,
            }
            self.armadillo_client.upsert_user(user_payload)
            count += 1

            if self.keycloak_sync_enabled:
                try:
                    self.keycloak_client.ensure_user(
                        email=user.email,
                        name=getattr(user, "name", "") or user.email,
                        is_superuser=bool(user.is_superuser),
                        project_slug=slugify(project.name) or f"project-{project.id}",
                        role=membership.role,
                    )
                except Exception:
                    logger.exception("Keycloak sync failed for user %s", user.email)

        return count

    def _sync_armadillo_project(self, project: Project, armadillo_project: str) -> int:
        member_emails = list(
            project.memberships.select_related("user").values_list(
                "user__email",
                flat=True,
            ),
        )
        payload = {
            "name": armadillo_project,
            "users": sorted(set(member_emails)),
        }
        self.armadillo_client.upsert_project(payload)
        return 1

    def _write_dataset_parquet(
        self,
        project: Project,
        dataset_template: AnalysisDatasetTemplate,
        exported_at,
    ) -> tuple[Path, str, int, int, str]:
        target_study = dataset_template.target_study
        builder = dataset_exports.TargetStudyDatasetBuilder(
            target_study=target_study,
            outcome_attributes=dataset_template.outcome_attributes.order_by("display_name", "variable_name"),
            confounder_attributes=dataset_template.confounder_attributes.order_by("display_name", "variable_name"),
            climate_attributes=dataset_template.climate_attributes.order_by("display_name", "variable_name"),
            location_attributes=dataset_template.location_attributes.order_by("display_name", "variable_name"),
            dataset_shape=dataset_template.dataset_shape,
            source_studies=dataset_template.source_studies.order_by("name"),
            max_lag_value=dataset_template.max_lag_value,
            deidentify=dataset_template.deidentify,
            exported_at=exported_at,
        )
        artifact = builder.build_artifact()

        dataframe = pd.DataFrame(artifact.rows, columns=artifact.columns)
        dataframe = self._prepare_armadillo_dataframe(dataframe, dataset_template)

        run_dir = (
            self.export_root
            / slugify(project.name or str(project.id))
            / exported_at.strftime("%Y%m%dT%H%M%S")
        )
        parquet_path = run_dir / artifact.naming.structured_slug / TABLE_OBJECT_FILENAME
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_parquet(parquet_path, index=False)

        object_name = self._build_object_name(target_study, artifact.naming.structured_slug)
        attribute_count = (
            dataset_template.outcome_attributes.count()
            + dataset_template.confounder_attributes.count()
            + dataset_template.climate_attributes.count()
            + dataset_template.location_attributes.count()
        )

        return parquet_path, object_name, attribute_count, len(artifact.rows), artifact.naming.structured_slug

    def _armadillo_project_name(self, project: Project) -> str:
        return dataset_exports.safe_slug(project.name, f"project-{project.id}")

    def _build_object_name(self, target_study, dataset_slug: str) -> str:
        return f"{dataset_slug}/{TABLE_OBJECT_FILENAME}"

    def _build_table_name(self, target_study, dataset_slug: str) -> str:
        return f"{dataset_slug}/{TABLE_RESOURCE_NAME}"

    def _prepare_armadillo_dataframe(
        self,
        dataframe: pd.DataFrame,
        dataset_template: AnalysisDatasetTemplate,
    ) -> pd.DataFrame:
        normalized = dataframe.copy()

        if dataset_template.dataset_shape == "wide":
            return self._prepare_wide_armadillo_dataframe(
                normalized,
                dataset_template,
            )

        return self._prepare_long_armadillo_dataframe(normalized)

    def _prepare_wide_armadillo_dataframe(
        self,
        dataframe: pd.DataFrame,
        dataset_template: AnalysisDatasetTemplate,
    ) -> pd.DataFrame:
        string_columns = [
            "project_name",
            "target_study_name",
            "patient_id",
            "outcome_variable_name",
            "outcome_display_name",
            "outcome_datetime",
            "outcome_location_name",
            "climate_lag_unit",
            "exported_at",
        ]
        int_columns = ["project_id", "target_study_id"]

        for column_name in string_columns:
            if column_name in dataframe.columns:
                dataframe[column_name] = self._coerce_string_series(dataframe[column_name])

        for column_name in int_columns:
            if column_name in dataframe.columns:
                dataframe[column_name] = self._coerce_int_series(dataframe[column_name])

        outcome_attribute = dataset_template.outcome_attributes.order_by(
            "display_name",
            "variable_name",
        ).first()
        if outcome_attribute and "outcome_value" in dataframe.columns:
            dataframe["outcome_value"] = self._coerce_series_for_attribute_type(
                dataframe["outcome_value"],
                outcome_attribute.variable_type,
            )
        elif "outcome_value" in dataframe.columns:
            dataframe["outcome_value"] = self._coerce_string_series(dataframe["outcome_value"])

        for attribute in dataset_template.confounder_attributes.order_by(
            "display_name",
            "variable_name",
        ):
            column_name = f"confounder__{attribute.variable_name}"
            if column_name in dataframe.columns:
                dataframe[column_name] = self._coerce_series_for_attribute_type(
                    dataframe[column_name],
                    attribute.variable_type,
                )

        for attribute in dataset_template.location_attributes.order_by(
            "display_name",
            "variable_name",
        ):
            column_name = f"location__{attribute.variable_name}"
            if column_name in dataframe.columns:
                dataframe[column_name] = self._coerce_series_for_attribute_type(
                    dataframe[column_name],
                    attribute.variable_type,
                )

        for attribute in dataset_template.climate_attributes.order_by(
            "display_name",
            "variable_name",
        ):
            column_prefix = f"climate__{attribute.variable_name}_"
            for column_name in [
                existing_name
                for existing_name in dataframe.columns
                if existing_name.startswith(column_prefix)
            ]:
                dataframe[column_name] = self._coerce_series_for_attribute_type(
                    dataframe[column_name],
                    attribute.variable_type,
                )

        return dataframe

    def _prepare_long_armadillo_dataframe(
        self,
        dataframe: pd.DataFrame,
    ) -> pd.DataFrame:
        int_columns = [
            "project_id",
            "target_study_id",
            "lag_value",
            "outcome_observation_id",
            "context_observation_id",
        ]
        string_columns = [
            column_name
            for column_name in dataframe.columns
            if column_name not in int_columns
        ]

        for column_name in int_columns:
            if column_name in dataframe.columns:
                dataframe[column_name] = self._coerce_int_series(dataframe[column_name])

        for column_name in string_columns:
            dataframe[column_name] = self._coerce_string_series(dataframe[column_name])

        return dataframe

    def _coerce_series_for_attribute_type(
        self,
        series: pd.Series,
        variable_type: str | None,
    ) -> pd.Series:
        if variable_type == "int":
            return self._coerce_int_series(series)
        if variable_type == "float":
            return self._coerce_float_series(series)
        if variable_type == "boolean":
            return self._coerce_boolean_series(series)
        if variable_type == "datetime":
            return self._coerce_datetime_series(series)
        return self._coerce_string_series(series)

    def _coerce_string_series(self, series: pd.Series) -> pd.Series:
        return series.fillna("").map(lambda value: "" if value is None else str(value))

    def _coerce_int_series(self, series: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(series.replace("", pd.NA), errors="coerce")
        return numeric.astype("Int32")

    def _coerce_float_series(self, series: pd.Series) -> pd.Series:
        return pd.to_numeric(series.replace("", pd.NA), errors="coerce").astype("float64")

    def _coerce_boolean_series(self, series: pd.Series) -> pd.Series:
        def _to_bool(value):
            if value is None or value is pd.NA:
                return pd.NA
            if isinstance(value, str) and value.strip() == "":
                return pd.NA
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "y"}:
                    return True
                if normalized in {"false", "0", "no", "n"}:
                    return False
            return bool(value)

        return series.map(_to_bool).astype("boolean")

    def _coerce_datetime_series(self, series: pd.Series) -> pd.Series:
        return pd.to_datetime(
            series.replace("", pd.NA),
            errors="coerce",
            utc=True,
        )

    def _legacy_object_names(
        self,
        target_study,
        dataset_slug: str,
        *,
        current_object_name: str,
    ) -> list[str]:
        target_slug = dataset_exports.safe_slug(
            target_study.name,
            f"target-study-{target_study.id}",
        )
        legacy_candidates = [
            f"{target_slug}/{dataset_slug}/{TABLE_OBJECT_FILENAME}",
            f"{target_slug}/{dataset_slug}",
            dataset_slug,
        ]
        return [name for name in legacy_candidates if name != current_object_name]

    def get_template_object_binding(self, template: AnalysisDatasetTemplate) -> dict[str, str]:
        project_name = self._armadillo_project_name(template.project)
        object_name = self._build_object_name(template.target_study, template.name)
        table_name = self._build_table_name(template.target_study, template.name)
        return {
            "project_name": project_name,
            "object_name": object_name,
            "table_name": table_name,
            "object_path": f"{project_name}/{object_name}",
            "table_path": f"{project_name}/{table_name}",
        }

    def delete_object_binding(self, *, project_name: str, object_name: str) -> None:
        self.armadillo_client.delete_object(
            project_name=project_name,
            object_name=object_name,
        )

    def delete_template_object(self, template: AnalysisDatasetTemplate) -> dict[str, str]:
        binding = self.get_template_object_binding(template)
        self.delete_object_binding(
            project_name=binding["project_name"],
            object_name=binding["object_name"],
        )
        return binding


def _serialize_observation_value(observation: Observation) -> str:
    attr_type = getattr(observation.attribute, "variable_type", None)
    preferred_candidates = []
    if attr_type == "float":
        preferred_candidates.append(observation.float_value)
    elif attr_type == "int":
        preferred_candidates.append(observation.int_value)
    elif attr_type in {"string", "categorical"}:
        preferred_candidates.append(observation.text_value)
    elif attr_type == "boolean":
        preferred_candidates.append(observation.boolean_value)
    elif attr_type == "datetime":
        preferred_candidates.append(observation.datetime_value)

    fallback_candidates = (
        observation.float_value,
        observation.int_value,
        observation.text_value,
        observation.boolean_value,
        observation.datetime_value,
    )

    value = next(
        (
            candidate
            for candidate in preferred_candidates + list(fallback_candidates)
            if candidate is not None and candidate != ""
        ),
        None,
    )

    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _serialize_time_dimension(time_dimension) -> str:
    if not time_dimension:
        return ""
    if getattr(time_dimension, "timestamp", None):
        return time_dimension.timestamp.isoformat()

    start = getattr(time_dimension, "start_date", None)
    end = getattr(time_dimension, "end_date", None)
    if start and end and start != end:
        return f"{start.isoformat()} / {end.isoformat()}"
    if start:
        return start.isoformat()
    if end:
        return end.isoformat()
    return ""


def _hash_identifier(value: str) -> str:
    if not value:
        return ""
    token = base64.urlsafe_b64encode(value.encode("utf-8")).decode("utf-8")
    return token[:16]


class ArmadilloClient:
    def __init__(self, base_url: str, username: str, password: str):
        _validate_http_base_url(base_url)
        self.base_url = base_url.rstrip("/")
        credentials = f"{username}:{password}".encode()
        self.auth_header = f"Basic {base64.b64encode(credentials).decode('utf-8')}"

    def upsert_user(self, payload: dict[str, Any]) -> None:
        self._request_json("/access/users", method="PUT", payload=payload)

    def upsert_project(self, payload: dict[str, Any]) -> None:
        self._request_json("/access/projects", method="PUT", payload=payload)

    def replace_object(
        self,
        project_name: str,
        object_name: str,
        file_path: Path,
    ) -> None:
        encoded_object = parse.quote(object_name, safe="")
        encoded_project = parse.quote(project_name, safe="")
        self._request(
            f"/storage/projects/{encoded_project}/objects/{encoded_object}",
            method="DELETE",
            body=None,
            expected=(204, 404),
        )

        body, content_type = _encode_multipart(
            fields={"object": object_name},
            file_field_name="file",
            file_path=file_path,
        )
        self._request(
            f"/storage/projects/{encoded_project}/objects",
            method="POST",
            body=body,
            content_type=content_type,
            expected=(204,),
        )

    def delete_object(
        self,
        project_name: str,
        object_name: str,
    ) -> None:
        encoded_object = parse.quote(object_name, safe="")
        encoded_project = parse.quote(project_name, safe="")
        self._request(
            f"/storage/projects/{encoded_project}/objects/{encoded_object}",
            method="DELETE",
            body=None,
            expected=(204, 404),
        )

    def _request_json(self, path: str, method: str, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._request(
            path=path,
            method=method,
            body=body,
            content_type="application/json",
            expected=(204,),
        )

    def _request(
        self,
        path: str,
        method: str,
        body: bytes | None,
        content_type: str | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> bytes:
        url = f"{self.base_url}{path}"
        req = request.Request(url, data=body, method=method)
        req.add_header("Authorization", self.auth_header)
        if content_type:
            req.add_header("Content-Type", content_type)

        try:
            with request.urlopen(req, timeout=30) as resp:
                if resp.status not in expected:
                    message = (
                        "Armadillo API unexpected status "
                        f"{resp.status} for {method} {path}"
                    )
                    raise RuntimeError(message)
                return resp.read()
        except error.HTTPError as exc:
            if exc.code in expected:
                return exc.read()
            details = exc.read().decode("utf-8", errors="ignore")
            message = (
                f"Armadillo API error {exc.code} "
                f"for {method} {path}: {details}"
            )
            raise RuntimeError(message) from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            message = (
                "Unable to reach Armadillo API at "
                f"{self.base_url}. Confirm the Armadillo service is running "
                "and the ANALYSIS_ARMADILLO_BASE_URL setting is correct. "
                f"Underlying error: {reason}"
            )
            raise RuntimeError(message) from exc


def _encode_multipart(
    fields: dict[str, str],
    file_field_name: str,
    file_path: Path,
) -> tuple[bytes, str]:
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for key, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ],
        )

    file_bytes = file_path.read_bytes()
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field_name}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ],
    )

    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class KeycloakAdminClient:
    def __init__(
        self,
        base_url: str,
        realm: str,
        admin_username: str,
        admin_password: str,
    ):
        self.base_url = base_url.rstrip("/")
        _validate_http_base_url(self.base_url)
        self.realm = realm
        self.admin_username = admin_username
        self.admin_password = admin_password

    def ensure_user(
        self,
        email: str,
        name: str,
        *,
        is_superuser: bool,
        project_slug: str,
        role: str,
    ) -> None:
        token = self._get_admin_token()
        if self._find_user_by_email(email=email, token=token):
            return

        import secrets
        from django.core.mail import send_mail

        temporary_password = secrets.token_urlsafe(12)

        payload = {
            "email": email,
            "username": email,
            "enabled": True,
            "emailVerified": True,
            "firstName": name,
            "credentials": [
                {
                    "type": "password",
                    "value": temporary_password,
                    "temporary": True,
                },
            ],
            "attributes": {
                "harmonaize_project": [project_slug],
                "harmonaize_role": [role],
                "harmonaize_superuser": ["true" if is_superuser else "false"],
            },
        }
        self._admin_request(
            f"/admin/realms/{parse.quote(self.realm, safe='')}/users",
            token=token,
            method="POST",
            payload=payload,
            expected=(201, 409),
        )

        # After user creation, map the Armadillo standard Realm Roles
        # Armadillo UI restricts access unless the user holds ROLE_RESEARCHER or ROLE_SU
        try:
            # 1. Look up the created user's ID
            user_list_resp = self._admin_request(
                f"/admin/realms/{parse.quote(self.realm, safe='')}/users?email={parse.quote(email, safe='')}",
                token=token,
                method="GET",
                payload=None,
                expected=(200,),
            )
            users = json.loads(user_list_resp.decode("utf-8"))
            if users:
                user_id = users[0]["id"]
                
                # 2. Look up the realm roles
                roles_resp = self._admin_request(
                    f"/admin/realms/{parse.quote(self.realm, safe='')}/roles",
                    token=token,
                    method="GET",
                    payload=None,
                    expected=(200,),
                )
                realm_roles = json.loads(roles_resp.decode("utf-8"))
                
                roles_to_map = []
                for r in realm_roles:
                    if r["name"] == "ROLE_RESEARCHER":
                        roles_to_map.append(r)
                    elif is_superuser and r["name"] == "ROLE_SU":
                        roles_to_map.append(r)
                
                # 3. Assign the roles
                if roles_to_map:
                    self._admin_request(
                        f"/admin/realms/{parse.quote(self.realm, safe='')}/users/{user_id}/role-mappings/realm",
                        token=token,
                        method="POST",
                        payload=roles_to_map,
                        expected=(204,),
                    )
        except Exception:
            logger.exception("Failed to map Armadillo UI roles for user %s", email)

        try:
            send_mail(
                subject=f"Welcome to HarmonAIze Keycloak - {project_slug}",
                message=(
                    f"Hi {name},\n\n"
                    f"Your account for DataSHIELD / Armadillo has been created.\n"
                    f"Username / Email: {email}\n"
                    f"Temporary Password: {temporary_password}\n\n"
                    f"You will be required to reset this password upon your first login.\n"
                    f"Armadillo URL: {getattr(settings, 'ANALYSIS_ARMADILLO_BASE_URL', 'http://localhost:8001')}\n"
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@harmonaize.com"),
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Failed to send Keycloak welcome email to %s", email)

    def _find_user_by_email(self, email: str, token: str) -> bool:
        encoded_realm = parse.quote(self.realm, safe="")
        path = (
            f"/admin/realms/{encoded_realm}/users"
            f"?email={parse.quote(email, safe='')}"
        )
        response = self._admin_request(
            path,
            token=token,
            method="GET",
            payload=None,
            expected=(200,),
        )
        users = json.loads(response.decode("utf-8") or "[]")
        return bool(users)

    def _get_admin_token(self) -> str:
        body = parse.urlencode(
            {
                "client_id": "admin-cli",
                "username": self.admin_username,
                "password": self.admin_password,
                "grant_type": "password",
            },
        ).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/realms/master/protocol/openid-connect/token",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["access_token"]

    def _admin_request(
        self,
        path: str,
        token: str,
        method: str,
        payload: dict[str, Any] | None,
        expected: tuple[int, ...],
    ) -> bytes:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = request.Request(f"{self.base_url}{path}", data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        if payload is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with request.urlopen(req, timeout=30) as response:
                if response.status not in expected:
                    message = (
                        "Keycloak API unexpected status "
                        f"{response.status} for {method} {path}"
                    )
                    raise RuntimeError(message)
                return response.read()
        except error.HTTPError as exc:
            if exc.code in expected:
                return exc.read()
            details = exc.read().decode("utf-8", errors="ignore")
            message = (
                f"Keycloak API error {exc.code} "
                f"for {method} {path}: {details}"
            )
            raise RuntimeError(message) from exc


def _validate_http_base_url(base_url: str) -> None:
    parsed = parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        message = "Only http/https URLs are supported for analysis sync services"
        raise ValueError(message)


def get_analysis_service_status() -> dict[str, dict[str, str | bool]]:
    armadillo_url = getattr(
        settings,
        "ANALYSIS_ARMADILLO_BASE_URL",
        "http://armadillo:8080",
    )
    parsed = parse.urlparse(armadillo_url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if not host:
        return {
            "armadillo": {
                "reachable": False,
                "detail": "Armadillo URL is missing a hostname.",
                "url": armadillo_url,
            },
        }

    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return {
            "armadillo": {
                "reachable": False,
                "detail": (
                    "Cannot resolve the Armadillo hostname from the Django/Celery "
                    f"containers ({host}:{port}). {exc}"
                ),
                "url": armadillo_url,
            },
        }

    return {
        "armadillo": {
            "reachable": True,
            "detail": f"Armadillo hostname {host}:{port} resolves correctly.",
            "url": armadillo_url,
        },
    }


def sync_scheduler_configuration(config: AnalysisScheduleConfiguration) -> None:
    """Synchronise scheduler UI state to django-celery-beat objects."""
    task_name = "analysis.scheduled.incremental_export"

    if not config.enabled:
        PeriodicTask.objects.filter(name=task_name).update(enabled=False)
        return

    interval, _ = IntervalSchedule.objects.get_or_create(
        every=config.interval_minutes,
        period=IntervalSchedule.MINUTES,
    )

    PeriodicTask.objects.update_or_create(
        name=task_name,
        defaults={
            "task": "analysis.tasks.run_scheduled_analysis_export",
            "interval": interval,
            "enabled": True,
            "kwargs": json.dumps({}),
        },
    )

    logger.info("Updated analysis scheduler: every %s minutes", config.interval_minutes)
